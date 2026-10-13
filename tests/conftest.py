"""Shared fixtures: the JVM, a GPU check and small synthetic images.

Tests marked ``jvm`` start a real JVM (the first run downloads Java 21 and the
Maven tree); tests marked ``gpu`` also run a real deconvolution and are skipped
when no OpenCL device is found. Neither can run on GitHub's hosted runners --
run them locally with ``uv run pytest``, or skip them with
``uv run pytest -m "not jvm"``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scyjava

from bdvpg_deconvolution.pipeline import hard_exit, init_imagej

# Physical calibration written into every synthetic OME-TIFF.
CALIBRATION = {
    "PhysicalSizeX": 0.1, "PhysicalSizeXUnit": "µm",
    "PhysicalSizeY": 0.1, "PhysicalSizeYUnit": "µm",
    "PhysicalSizeZ": 0.3, "PhysicalSizeZUnit": "µm",
}

# Bright points of the synthetic image, as (channel, z, y, x).
POINTS = [(0, 8, 20, 20), (0, 8, 40, 44), (1, 5, 32, 32)]


# --- JVM lifetime ------------------------------------------------------------

_exitstatus = 0


def pytest_sessionfinish(session, exitstatus):
    global _exitstatus
    _exitstatus = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # ImageJ leaves non-daemon AWT threads behind, so a process that started
    # the JVM never exits on its own (see pipeline.hard_exit). The report is
    # already printed by now; keep pytest's exit status.
    if scyjava.jvm_started():
        hard_exit(_exitstatus)


@pytest.fixture(scope="session")
def ij():
    return init_imagej(mode="headless")


@pytest.fixture(scope="session")
def gpu_devices(ij):
    from bdvpg_deconvolution.gpu import available_devices

    devices = available_devices()
    if not devices:
        pytest.skip("no OpenCL device available")
    return devices


# --- Synthetic data ----------------------------------------------------------

def _gaussian(shape, sigma):
    g = np.ones(shape)
    for axis, (n, s) in enumerate(zip(shape, sigma)):
        x = np.arange(n) - (n - 1) / 2
        g = g * np.exp(-x**2 / (2 * s**2)).reshape(
            [-1 if i == axis else 1 for i in range(len(shape))])
    return g / g.sum()


def _blur(volume, psf):
    """Circular convolution of a ZYX volume with a centred PSF."""
    kernel = np.zeros(volume.shape)
    kernel[tuple(slice(0, n) for n in psf.shape)] = psf
    kernel = np.roll(kernel, [-(n // 2) for n in psf.shape], axis=(0, 1, 2))
    return np.real(np.fft.ifftn(np.fft.fftn(volume) * np.fft.fftn(kernel)))


def _write_ome(path, series):
    """Write ``[(name, CZYX array), ...]`` as one OME-TIFF, a series each."""
    import tifffile

    with tifffile.TiffWriter(path, ome=True) as tif:
        for name, data in series:
            tif.write(data, photometric="minisblack",
                      metadata={"axes": "CZYX", "Name": name, **CALIBRATION})


@pytest.fixture(scope="session")
def data(tmp_path_factory):
    """Two PSFs, a 2-channel single-series image, and a 2-series image.

    The image is blurred with ``psf``; ``psf_narrow`` is a second, narrower
    one, different enough to tell from the result which PSF a channel got.
    """
    import tifffile

    root = tmp_path_factory.mktemp("data")
    psf = _gaussian((9, 15, 15), (1.5, 1.5, 1.5))
    for name, kernel in [("psf", psf), ("psf_narrow", _gaussian((9, 15, 15), (0.7, 0.7, 0.7)))]:
        tifffile.imwrite(root / f"{name}.tif", kernel.astype(np.float32)[np.newaxis],
                         ome=True, photometric="minisblack",
                         metadata={"axes": "CZYX", **CALIBRATION})

    points = np.zeros((2, 16, 64, 64))
    for p in POINTS:
        points[p] = 1000.0
    image = np.stack([_blur(c, psf) for c in points]) * 20 + 10
    image = image.astype(np.uint16)

    _write_ome(root / "single.tif", [("single", image)])
    # Series 1 has its own shape, so a test can tell which one was processed.
    _write_ome(root / "multi.tif", [("First pos", image[:1]),
                                    ("Second pos", image[:, :12, :48, :40])])
    return {"root": root, "psf": root / "psf.tif",
            "psf_narrow": root / "psf_narrow.tif", "image": image,
            "single": root / "single.tif", "multi": root / "multi.tif"}


def read_ome(path: str | Path):
    """Return ``(axes, array)`` of the full-resolution series of an OME-TIFF."""
    import tifffile

    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        return series.axes, series.asarray()
