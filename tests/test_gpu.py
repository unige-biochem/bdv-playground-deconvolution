"""End-to-end deconvolutions on the GPU, on small synthetic images.

Skipped when no OpenCL device is found. Uses whatever GPU pool is configured
in the ImageJ preferences, and never changes it (it is persistent, shared
with Fiji -- see bdvpg_deconvolution.gpu).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import POINTS, read_ome

from bdvpg_deconvolution.pipeline import DeconvolveParams, run

pytestmark = [pytest.mark.jvm, pytest.mark.gpu]

# Small blocks so the 64x64x16 image spans several of them, overlaps included.
FAST = dict(block_size_x=32, block_size_y=32, block_size_z=16, overlap_size=4,
            num_iterations=20, n_threads=2)


def deconvolve(data, tmp_path, image="single", **kwargs):
    return run(DeconvolveParams(image_file=str(data[image]),
                                psf_file=str(data["psf"]),
                                output_folder=str(tmp_path), **FAST, **kwargs))


def test_single_series(gpu_devices, data, tmp_path):
    out = deconvolve(data, tmp_path)

    assert Path(out) == tmp_path / "single.ome.tiff"
    axes, result = read_ome(out)
    image = data["image"]
    assert axes == "CZYX"
    assert result.shape == image.shape
    assert result.dtype == image.dtype  # "Keep Pixel Type Of Original Image"
    # Deconvolution concentrates each blurred point back towards its centre.
    for p in POINTS:
        assert result[p] > 2 * image[p], p


def test_float_output(gpu_devices, data, tmp_path):
    _, result = read_ome(deconvolve(data, tmp_path, output_pixel_type="Float"))
    assert result.dtype == "float32"


def test_overwrite(gpu_devices, data, tmp_path):
    (tmp_path / "single.ome.tiff").write_bytes(b"previous result")
    out = deconvolve(data, tmp_path, overwrite=True, range_slices="0")
    _, result = read_ome(out)
    assert result.shape == (2, 64, 64)


@pytest.mark.parametrize("naming, suffix", [("name", "_Second_pos"), ("index", "_1")])
def test_multi_series_subrange(gpu_devices, data, tmp_path, naming, suffix):
    out = deconvolve(data, tmp_path, image="multi", series=1,
                     series_naming=naming, range_channels="1",
                     range_slices="0:3")

    assert Path(out) == tmp_path / f"multi{suffix}.ome.tiff"
    axes, result = read_ome(out)
    # Series 1 is 2 x 12 x 48 x 40 (CZYX): one channel and 4 slices kept.
    assert axes == "ZYX"
    assert result.shape == (4, 48, 40)
