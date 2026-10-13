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
