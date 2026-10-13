"""Tests against the real Java stack that need no GPU.

They start a JVM and open images through Bio-Formats, but every case stops
before the deconvolution itself.
"""

from __future__ import annotations

import pytest

from bdvpg_deconvolution import pipeline
from bdvpg_deconvolution.pipeline import (
    DeconvolveParams,
    _jclass,
    _run_command,
    _select_channels,
    _source_service,
    describe_series,
    run,
)
from bdvpg_deconvolution.smoke_test import CLASSES

pytestmark = pytest.mark.jvm


def test_runs_on_pinned_java(ij):
    System = _jclass("java.lang.System")
    assert str(System.getProperty("java.specification.version")) == pipeline.JAVA_VERSION
    # Provisioned through cjdk, not picked up from JAVA_HOME or PATH.
    assert "cjdk" in str(System.getProperty("java.home"))


@pytest.mark.parametrize("name", CLASSES)
def test_class_resolves(ij, name):
    _jclass(name)


def test_undeclared_command_input_is_refused(ij):
    export = _jclass("ch.epfl.biop.kheops.command.KheopsExportSourcesCommand")
    with pytest.raises(RuntimeError, match=r"no input\(s\) \['sacs'\]"):
        _run_command(ij.command(), export, {"sacs": None})


def params(data, tmp_path, image="single", **kwargs):
    return DeconvolveParams(image_file=str(data[image]), psf_file=str(data["psf"]),
                            output_folder=str(tmp_path), **kwargs)


def test_multi_series_requires_a_choice(ij, data, tmp_path):
    with pytest.raises(ValueError, match="contains 2 series") as err:
        run(params(data, tmp_path, image="multi"), ij=ij)
    assert "First pos" in str(err.value)
    assert "Second pos" in str(err.value)
    # The failed run leaves the file opened, so its series can be listed.
    series = describe_series(_source_service(ij), "multi")
    assert series == [(0, "First pos", 1), (1, "Second pos", 2)]


def test_multi_series_index_out_of_range(ij, data, tmp_path):
    with pytest.raises(ValueError, match="series=2 is out of range"):
        run(params(data, tmp_path, image="multi", series=2), ij=ij)


def test_single_series_refuses_other_index(ij, data, tmp_path):
    with pytest.raises(ValueError, match="holds a single series"):
        run(params(data, tmp_path, series=1), ij=ij)


def test_existing_output_is_kept_without_overwrite(ij, data, tmp_path):
    existing = tmp_path / "single.ome.tiff"
    existing.write_bytes(b"previous result")
    with pytest.raises(FileExistsError):
        run(params(data, tmp_path), ij=ij)
    assert existing.read_bytes() == b"previous result"


def test_ome_tif_input_names_output_without_double_ome(ij, data, tmp_path):
    image = tmp_path / "cells.ome.tif"
    image.write_bytes(data["single"].read_bytes())
    out = tmp_path / "out"
    out.mkdir()
    (out / "cells.ome.tiff").write_bytes(b"previous result")
    # Refusing to overwrite proves the name it would have written to.
    with pytest.raises(FileExistsError, match=r"cells\.ome\.tiff"):
        run(DeconvolveParams(image_file=str(image), psf_file=str(data["psf"]),
                             output_folder=str(out)), ij=ij)


# --- One PSF per channel -----------------------------------------------------

@pytest.mark.parametrize("expression, n, expected", [
    ("", 3, [0, 1, 2]),
    ("1", 3, [1]),
    ("2,0", 3, [2, 0]),
    ("0:-1", 2, [0, 1]),
    ("0:2:4", 5, [0, 2, 4]),
    # The examples of the readme's range table.
    ("2", 6, [2]),
    ("0,2,5", 6, [0, 2, 5]),
    ("0:4", 6, [0, 1, 2, 3, 4]),
    ("0:2:8", 9, [0, 2, 4, 6, 8]),
    ("-1", 6, [5]),
    ("-1:-1:0", 4, [3, 2, 1, 0]),
    ("0:3,-1", 6, [0, 1, 2, 3, 5]),
])
def test_select_channels(ij, expression, n, expected):
    assert _select_channels(expression, n) == expected


# No "end" keyword, despite what IntRangeParser's own documentation says.
@pytest.mark.parametrize("expression", ["2", "0:5", "a", "end", "0:end", "0:2:end"])
def test_select_channels_refuses(ij, expression):
    with pytest.raises(ValueError, match="does not apply to 2 channel"):
        _select_channels(expression, 2)


def two_psfs(data):
    return [str(data["psf"]), str(data["psf_narrow"])]


@pytest.mark.parametrize("range_channels, exported", [("0", 1), ("0,1,0", 3)])
def test_psf_count_must_match_exported_channels(ij, data, tmp_path, capsys,
                                                range_channels, exported):
    p = params(data, tmp_path, range_channels=range_channels)
    p.psf_file = two_psfs(data)
    with pytest.raises(ValueError, match=fr"'single.tif' exports {exported} "
                                         r"channel\(s\).*2 PSF files"):
        run(p, ij=ij)
    # A single-series file has nothing else to warn about.
    assert "WARNING" not in capsys.readouterr().err


def test_single_psf_needs_no_count(ij, data, tmp_path):
    # Refusing to overwrite is the first step after the channels are paired.
    (tmp_path / "single.ome.tiff").write_bytes(b"previous result")
    with pytest.raises(FileExistsError):
        run(params(data, tmp_path, range_channels="1,0,1"), ij=ij)


def test_misfit_series_are_listed_before_the_choice(ij, data, tmp_path, capsys):
    p = params(data, tmp_path, image="multi")
    p.psf_file = two_psfs(data)
    with pytest.raises(ValueError, match="contains 2 series"):
        run(p, ij=ij)
    warning = capsys.readouterr().err
    assert "WARNING: 2 PSF files" in warning
    assert "1 of the 2 series of 'multi.tif' do not fit" in warning
    assert "0  First pos  (1 channel(s) exported)" in warning
    assert "Second pos" not in warning


def test_misfit_series_is_refused(ij, data, tmp_path, capsys):
    p = params(data, tmp_path, image="multi", series=0)
    p.psf_file = two_psfs(data)
    with pytest.raises(ValueError, match=r"Series 0 'First pos' of 'multi.tif' "
                                         r"exports 1 channel\(s\) \[0\]"):
        run(p, ij=ij)
    assert "First pos" in capsys.readouterr().err


def test_range_that_does_not_apply_is_listed(ij, data, tmp_path, capsys):
    # Channel 1 exists in series 1 only.
    p = params(data, tmp_path, image="multi", series=0, range_channels="1,0")
    p.psf_file = two_psfs(data)
    with pytest.raises(ValueError, match="does not apply to 1 channel"):
        run(p, ij=ij)
    assert "First pos  (range_channels='1,0' does not apply" in capsys.readouterr().err


def test_fitting_series_proceeds_after_the_warning(ij, data, tmp_path, capsys):
    p = params(data, tmp_path, image="multi", series=1)
    p.psf_file = two_psfs(data)
    (tmp_path / "multi_Second_pos.ome.tiff").write_bytes(b"previous result")
    with pytest.raises(FileExistsError):
        run(p, ij=ij)
    out = capsys.readouterr()
    assert "First pos" in out.err
    assert "channel 0 <- PSF 'psf.tif'" in out.out
    assert "channel 1 <- PSF 'psf_narrow.tif'" in out.out


@pytest.mark.parametrize("kwargs, message", [
    ({"save_output": False, "show_in_bdv": False}, "Nothing to do"),
    ({"output_folder": None}, "No output folder"),
    ({"series_naming": "position"}, "series_naming must be"),
])
def test_invalid_params(ij, data, tmp_path, kwargs, message):
    p = params(data, tmp_path)
    for key, value in kwargs.items():
        setattr(p, key, value)
    with pytest.raises(ValueError, match=message):
        run(p, ij=ij)
