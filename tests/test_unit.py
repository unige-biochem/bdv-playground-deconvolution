"""Fast tests of the Python-side logic. No JVM is started, no GPU is needed."""

from __future__ import annotations

import pytest
import scyjava

from bdvpg_deconvolution import cli, gpu, pipeline
from bdvpg_deconvolution.pipeline import (
    DeconvolveParams,
    _base_name,
    _format_series_listing,
    _sanitize_filename,
)


# --- Series naming -----------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Day4to5 - Position 5", "Day4to5_-_Position_5"),
    ('a/b\\c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j"),
    ("  padded  name  ", "padded_name"),
    ("x\ty", "x_y"),
    ("a__b", "a_b"),
    (".hidden.", "hidden"),
    ("...", "series"),
    ("", "series"),
])
def test_sanitize_filename(name, expected):
    assert _sanitize_filename(name) == expected


@pytest.mark.parametrize("file, expected", [
    ("cells.tif", "cells"),
    ("cells.czi", "cells"),
    ("cells.ome.tif", "cells"),
    ("cells.ome.tiff", "cells"),
    ("cells.OME.TIF", "cells"),
    ("cells.ome.btf", "cells"),
    ("cells.ome.zarr", "cells"),
    ("/data/run1/cells.ome.tif", "cells"),
    ("run.1.ome.tif", "run.1"),
    ("home.tif", "home"),
    ("cells", "cells"),
])
def test_base_name(file, expected):
    assert _base_name(file) == expected


def test_series_listing_names_file_and_channels():
    text = _format_series_listing("/data/run1/img.czi",
                                  [(0, "A", 1), (1, "Position 2", 3)])
    assert "'img.czi' contains 2 series" in text
    assert "(1 channel)" in text
    assert "(3 channels)" in text
    assert "--series <index>" in text


# --- GPU pool specification --------------------------------------------------

@pytest.mark.parametrize("spec", ["0:1", "0:2, 1:4", " 0 : 2 ,1:4 "])
def test_valid_pool_spec(spec):
    gpu.validate_pool_spec(spec, devices=["gpu A", "gpu B"])


@pytest.mark.parametrize("spec", ["", "0", "0:", "a:1", "0:1,", "0:1;1:2", "0-1"])
def test_malformed_pool_spec(spec):
    with pytest.raises(ValueError, match="Invalid GPU pool"):
        gpu.validate_pool_spec(spec, devices=["gpu A"])


def test_pool_spec_with_missing_device():
    with pytest.raises(ValueError, match=r"device\(s\) 2, but only 2"):
        gpu.validate_pool_spec("0:1, 2:1", devices=["gpu A", "gpu B"])


def test_pool_spec_unchecked_without_devices():
    # No device list to check against: CLIJ gives the authoritative failure.
    gpu.validate_pool_spec("5:1", devices=[])


def test_format_pool_spec():
    assert gpu.format_pool_spec([0, 1], [2, 4]) == "0:2, 1:4"


def test_gpu_cli_rejects_malformed_spec_before_jvm(monkeypatch, capsys):
    def no_jvm(**_):
        raise AssertionError("the JVM must not start for a malformed spec")

    monkeypatch.setattr(gpu, "init_imagej", no_jvm)
    assert gpu._main(["0-1"]) == 2
    assert "Invalid GPU pool" in capsys.readouterr().err


# --- Deconvolution CLI -------------------------------------------------------

@pytest.fixture
def fake_pipeline(monkeypatch):
    """Stub the JVM and the pipeline out of the CLI; record what it passes."""
    calls = {}

    def fake_init(**kwargs):
        calls["init"] = kwargs
        return "ij"

    def fake_run(params, ij=None):
        calls["params"] = params
        calls["ij"] = ij

    monkeypatch.setattr(cli, "init_imagej", fake_init)
    monkeypatch.setattr(cli, "run", fake_run)
    return calls


REQUIRED = ["--image", "img.tif", "--psf", "psf.tif", "--out", "out"]


def test_cli_defaults_match_params_defaults(fake_pipeline):
    assert cli._main(REQUIRED) == 0
    assert fake_pipeline["params"] == DeconvolveParams(
        image_file="img.tif", psf_file="psf.tif", output_folder="out")
    assert fake_pipeline["init"] == {"mode": "headless", "max_heap": None}
    assert fake_pipeline["ij"] == "ij"


def test_cli_maps_every_option(fake_pipeline):
    argv = REQUIRED + [
        "--overwrite",
        "--series", "2",
        "--series-naming", "index",
        "--unit", "NANOMETER",
        "--output-pixel-type", "Float",
        "--block-size-x", "128",
        "--block-size-y", "96",
        "--block-size-z", "32",
        "--overlap-size", "8",
        "--iterations", "40",
        "--no-non-circulant",
        "--regularization", "0.01",
        "--threads", "3",
        "--gpu-pool", "0:2, 1:4",
        "--compression", "Uncompressed",
        "--resolution-levels", "3",
        "--range-channels", "0,2",
        "--range-slices", "0:9",
        "--range-frames", "end",
        "--mode", "interactive",
        "--max-heap", "8g",
    ]
    assert cli._main(argv) == 0
    assert fake_pipeline["params"] == DeconvolveParams(
        image_file="img.tif",
        psf_file="psf.tif",
        output_folder="out",
        unit="NANOMETER",
        output_pixel_type="Float",
        block_size_x=128,
        block_size_y=96,
        block_size_z=32,
        overlap_size=8,
        num_iterations=40,
        non_circulant=False,
        regularization_factor=0.01,
        n_threads=3,
        show_in_bdv=False,
        save_output=True,
        compression="Uncompressed",
        n_resolution_levels=3,
        overwrite=True,
        series=2,
        series_naming="index",
        gpu_pool="0:2, 1:4",
        range_channels="0,2",
        range_slices="0:9",
        range_frames="end",
    )
    assert fake_pipeline["init"] == {"mode": "interactive", "max_heap": "8g"}


def test_cli_reports_pipeline_errors(monkeypatch, fake_pipeline, capsys):
    def failing_run(params, ij=None):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "run", failing_run)
    assert cli._main(REQUIRED) == 1
    assert "ERROR: boom" in capsys.readouterr().err


# --- JVM provisioning --------------------------------------------------------

@pytest.fixture
def java_constraints():
    """Restore scyjava's global Java constraints after the test."""
    saved = (scyjava.config.get_fetch_java(),
             scyjava.config.get_java_vendor(),
             scyjava.config.get_java_version())
    yield
    scyjava.config.set_java_constraints(
        fetch=saved[0], vendor=saved[1], version=saved[2])


def test_init_imagej_pins_cjdk_java(monkeypatch, java_constraints):
    class FakeGateway:
        def getVersion(self):
            return "fake"

    calls = []

    def fake_init(endpoints, mode):
        calls.append((endpoints, mode))
        return FakeGateway()

    monkeypatch.setattr(pipeline, "_ij", None)
    monkeypatch.setattr(pipeline.imagej, "init", fake_init)
    # A caller (or scyjava's own default) preferring a system Java ...
    scyjava.config.set_java_constraints(fetch="auto", vendor="zulu", version="11")

    pipeline.init_imagej()

    # ... is overridden: always the cjdk-provisioned, pinned runtime.
    assert scyjava.config.get_fetch_java() == "always"
    assert scyjava.config.get_java_vendor() == pipeline.JAVA_VENDOR
    assert scyjava.config.get_java_version() == pipeline.JAVA_VERSION
    assert calls == [(pipeline.DEFAULT_ENDPOINTS, "headless")]
