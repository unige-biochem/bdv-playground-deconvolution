"""End-to-end deconvolutions on the GPU, on small synthetic images.

Skipped when no OpenCL device is found. Uses whatever GPU pool is configured
in the ImageJ preferences, and never changes it (it is persistent, shared
with Fiji -- see bdvpg_deconvolution.gpu).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import POINTS, read_ome

from bdvpg_deconvolution.pipeline import DeconvolveParams, run

pytestmark = [pytest.mark.jvm, pytest.mark.gpu]

# Small blocks so the 64x64x16 image spans several of them, overlaps included.
FAST = dict(block_size_x=32, block_size_y=32, block_size_z=16, overlap_size=4,
            num_iterations=20, n_threads=2)


def deconvolve(data, tmp_path, image="single", psf=("psf",), **kwargs):
    psf_file = [str(data[p]) for p in psf]
    return run(DeconvolveParams(image_file=str(data[image]),
                                psf_file=psf_file[0] if len(psf) == 1 else psf_file,
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


# --- One PSF per channel -----------------------------------------------------
# Which PSF a channel got is checked by comparing it with a deconvolution by
# that PSF alone, which needs reproducible results. Non-circulant mode does not
# give them: clij2-fft pads each tile into an uncleared GPU buffer, so a
# result depends on what earlier tiles left in GPU memory. Circulant mode pads
# by mirroring, which writes every voxel, and is reproducible bit for bit.
EXACT = dict(non_circulant=False)


@pytest.fixture(scope="module")
def reference(gpu_devices, data, tmp_path_factory):
    """Both channels of each image, deconvolved with each PSF alone."""
    ref = {}
    for psf in ("psf", "psf_narrow"):
        for image, kwargs in [("single", {}), ("multi", {"series": 1})]:
            out = deconvolve(data, tmp_path_factory.mktemp(psf), image=image,
                             psf=(psf,), **kwargs, **EXACT)
            ref[image, psf] = read_ome(out)[1]
    return ref


def test_the_two_psfs_give_different_results(reference):
    # Otherwise the tests below could not tell which PSF a channel got.
    assert not np.array_equal(reference["single", "psf"],
                              reference["single", "psf_narrow"])


def test_one_psf_per_channel(reference, data, tmp_path):
    _, result = read_ome(deconvolve(data, tmp_path, psf=("psf", "psf_narrow"), **EXACT))
    np.testing.assert_array_equal(result[0], reference["single", "psf"][0])
    np.testing.assert_array_equal(result[1], reference["single", "psf_narrow"][1])


def test_psfs_follow_the_channel_selection(reference, data, tmp_path):
    # Exported channel 0 is image channel 1, so it gets the first PSF.
    _, result = read_ome(deconvolve(data, tmp_path, psf=("psf_narrow", "psf"),
                                    range_channels="1,0", **EXACT))
    np.testing.assert_array_equal(result[0], reference["single", "psf_narrow"][1])
    np.testing.assert_array_equal(result[1], reference["single", "psf"][0])


def test_psf_per_channel_on_a_series_slice(reference, data, tmp_path):
    out = deconvolve(data, tmp_path, image="multi", series=1,
                     psf=("psf_narrow", "psf"), range_channels="1,0",
                     range_slices="0:3", **EXACT)

    assert Path(out) == tmp_path / "multi_Second_pos.ome.tiff"
    axes, result = read_ome(out)
    # Series 1 is 2 x 12 x 48 x 40 (CZYX): both channels, swapped, 4 slices.
    assert axes == "CZYX"
    assert result.shape == (2, 4, 48, 40)
    np.testing.assert_array_equal(result[0], reference["multi", "psf_narrow"][1, :4])
    np.testing.assert_array_equal(result[1], reference["multi", "psf"][0, :4])
