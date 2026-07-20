"""Core deconvolution pipeline.

Tiled, lazy, multi-GPU Richardson-Lucy deconvolution of 5D images (XYZ +
channels + timepoints). Nothing is reimplemented
in Python: we start a JVM, pull the BIOP tools from Maven, and orchestrate the
ImageJ2 / BigDataViewer-Playground SciJava commands that do the actual work
(``DatasetFromBioFormatsCreateCommand``, ``SourcesDeconvolveCommand``,
``KheopsExportSourcesCommand``).

Laziness matters: setting up the deconvolution is cheap, and the block-by-block
GPU work is only triggered by browsing the sources or writing the output.

Derived from a Fiji/Groovy workflow by BIOP - EPFL (preserved on the
``specific-use-cases`` branch).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import imagej
import scyjava

# --- Java dependencies -------------------------------------------------------
# These two endpoints pull in everything transitively (BigDataViewer-Playground,
# Bio-Formats, Kheops, the BIOP deconvolve command and CLIJ2). Pin them for
# reproducibility. Override via init_imagej(endpoints=[...]) if needed.
DEFAULT_ENDPOINTS = [
    "net.imagej:imagej:2.16.0",
    "ch.epfl.biop:bigdataviewer-biop-tools:0.21.0",
]

# Module-level singleton: a JVM can only be started once per process.
_ij = None


def init_imagej(
    mode: str = "headless",
    endpoints: Optional[list[str]] = None,
    max_heap: Optional[str] = None,
    jvm_options: Optional[list[str]] = None,
):
    """Start (once) and return the ImageJ2 gateway.

    Parameters
    ----------
    mode
        ``"headless"`` for batch / CLI / Nextflow (no GUI, save-only).
        ``"interactive"`` to allow BigDataViewer windows (needs a display;
        non-blocking, good for notebooks). ``"gui"`` blocks on the UI.
    endpoints
        Maven coordinates to assemble the JVM classpath. Defaults to
        :data:`DEFAULT_ENDPOINTS`.
    max_heap
        Convenience for the JVM ``-Xmx`` option, e.g. ``"32g"``.
    jvm_options
        Extra raw JVM flags, e.g. ``["-Dfoo=bar"]``.
    """
    global _ij
    if _ij is not None:
        return _ij

    if max_heap:
        scyjava.config.add_option(f"-Xmx{max_heap}")
    for opt in jvm_options or []:
        scyjava.config.add_option(opt)

    _ij = imagej.init(endpoints or DEFAULT_ENDPOINTS, mode=mode)
    print(f"ImageJ2 {_ij.getVersion()} started (mode={mode})")
    return _ij


# --- Java interop helpers ----------------------------------------------------

def _jclass(name: str):
    return scyjava.jimport(name)


def _sac_array(sources):
    """Build a typed ``SourceAndConverter[]`` from a Python/Java list.

    The BIOP commands declare their inputs as ``SourceAndConverter[]`` (not a
    List), so a real typed Java array is required -- a Python list would be
    converted to an ArrayList and rejected.
    """
    SAC = _jclass("bdv.viewer.SourceAndConverter")
    items = list(sources)
    arr = scyjava.jarray(SAC, len(items))
    for i, s in enumerate(items):
        arr[i] = s
    return arr


def _file_array(paths):
    File = _jclass("java.io.File")
    arr = scyjava.jarray(File, len(paths))
    for i, p in enumerate(paths):
        arr[i] = File(str(Path(p).resolve()))
    return arr


def _source_service(ij):
    """The BDV-Playground SourceService, which turns SpimData into sources."""
    SourceService = _jclass("sc.fiji.bdvpg.scijava.service.SourceService")
    return ij.context().getService(SourceService)


# --- Parameters --------------------------------------------------------------

@dataclass
class DeconvolveParams:
    image_file: str
    psf_file: str
    output_folder: Optional[str] = None
    unit: str = "MICROMETER"  # MICROMETER | MILLIMETER | NANOMETER | PIXEL
    output_pixel_type: str = "Keep Pixel Type Of Original Image"  # or "Float"
    block_size_x: int = 256
    block_size_y: int = 256
    block_size_z: int = 64
    overlap_size: int = 16
    num_iterations: int = 120
    non_circulant: bool = True
    regularization_factor: float = 0.0
    n_threads: int = 10
    show_in_bdv: bool = False  # default off for headless/CLI use
    save_output: bool = True
    compression: str = "LZW"  # LZW | Uncompressed | JPEG-2000 | JPEG-2000 Lossy | JPEG
    n_resolution_levels: int = 1
    overwrite: bool = False


# --- The pipeline ------------------------------------------------------------

def run(params: DeconvolveParams, ij=None):
    """Run the deconvolution pipeline. Returns the output OME-TIFF path or None."""
    if ij is None:
        ij = init_imagej()

    cs = ij.command()
    source_service = _source_service(ij)

    Float = _jclass("java.lang.Float")
    File = _jclass("java.io.File")
    FilenameUtils = _jclass("org.apache.commons.io.FilenameUtils")

    DatasetFromBioFormatsCreateCommand = _jclass(
        "ch.epfl.biop.bdv.img.bioformats.command.DatasetFromBioFormatsCreateCommand"
    )
    SourcesDeconvolveCommand = _jclass(
        "ch.epfl.biop.command.process.deconvolve.SourcesDeconvolveCommand"
    )
    KheopsExportSourcesCommand = _jclass(
        "ch.epfl.biop.kheops.command.KheopsExportSourcesCommand"
    )

    # ---- 0. Resolve and validate the output file ---------------------------
    image_file = Path(params.image_file).resolve()
    psf_file = Path(params.psf_file).resolve()
    image_name = FilenameUtils.removeExtension(image_file.name)
    psf_name = FilenameUtils.removeExtension(psf_file.name)

    if not params.show_in_bdv and not params.save_output:
        raise ValueError(
            "Nothing to do: enable show_in_bdv, save_output, or both."
        )

    output_file = None
    if params.save_output:
        if params.output_folder is None:
            raise ValueError(
                "No output folder chosen: set output_folder, or disable "
                "save_output to only view the result."
            )
        output_file = File(str(Path(params.output_folder).resolve()),
                           image_name + ".ome.tiff")
        if output_file.exists():
            if params.overwrite:
                if not output_file.delete():
                    raise RuntimeError(
                        f"Could not delete pre-existing output file: {output_file}"
                    )
            else:
                raise FileExistsError(
                    f"Output file already exists (set overwrite=True to replace): "
                    f"{output_file}"
                )

    # The OME-TIFF exporter only accepts MILLIMETER or MICROMETER; map the rest.
    export_unit = "MILLIMETER" if params.unit == "MILLIMETER" else "MICROMETER"

    def open_sources(file: Path, name: str):
        spimdata = (
            cs.run(
                DatasetFromBioFormatsCreateCommand, True,
                {
                    "datasetname": name,
                    "unit": params.unit,
                    "files": _file_array([file]),
                    "split_rgb_channels": False,
                    "auto_pyramidize": True,
                    "plane_origin_convention": "TOP LEFT",
                    "disable_memo": False,
                },
            )
            .get()
            .getOutput("spimdata")
        )
        return source_service.getSourcesFromDataset(spimdata)

    # ---- 1. Open the image and the PSF -------------------------------------
    image_sources = open_sources(image_file, image_name)
    psf_sources = open_sources(psf_file, psf_name)

    if image_sources.isEmpty():
        raise RuntimeError(f"No source found in image file: {image_file}")
    if psf_sources.isEmpty():
        raise RuntimeError(f"No source found in PSF file: {psf_file}")

    psf_source = psf_sources.get(0)  # one PSF for all channels

    print(f"Opened '{image_name}' : {image_sources.size()} channel(s)")
    print(f"Opened PSF '{psf_name}' : {psf_sources.size()} source(s), "
          f"using the first one")

    # ---- 2. Optionally show the raw sources in BDV -------------------------
    SourceServices = None
    if params.show_in_bdv:
        SourceServices = _jclass("sc.fiji.bdvpg.service.SourceServices")
        bdv_raw = SourceServices.getBdvDisplayService().getNewBdv()
        SourceServices.getBdvDisplayService().show(bdv_raw, _sac_array(image_sources))

    # ---- 3. Run the tiled, lazy Richardson-Lucy GPU deconvolution ----------
    deconvolved = (
        cs.run(
            SourcesDeconvolveCommand, True,
            {
                "sources": _sac_array(image_sources),
                "psf": psf_source,
                "output_pixel_type": params.output_pixel_type,
                "suffix": "_deconvolved",
                "block_size_x": params.block_size_x,
                "block_size_y": params.block_size_y,
                "block_size_z": params.block_size_z,
                "overlap_size": params.overlap_size,
                "num_iterations": params.num_iterations,
                "non_circulant": params.non_circulant,
                "regularization_factor": Float(float(params.regularization_factor)),
                "n_threads": params.n_threads,
            },
        )
        .get()
        .getOutput("sources_out")
    )

    print(f"Deconvolution set up for {len(deconvolved)} channel(s) "
          f"(computed lazily on export)")

    # ---- 4. Optionally show the deconvolved sources in a second BDV --------
    if params.show_in_bdv:
        bdv_dec = SourceServices.getBdvDisplayService().getNewBdv()
        SourceServices.getBdvDisplayService().show(bdv_dec, deconvolved)

    # ---- 5. Optionally export the deconvolved sources to OME-TIFF ----------
    result_path = None
    if params.save_output:
        cs.run(
            KheopsExportSourcesCommand, True,
            {
                "sacs": deconvolved,
                "range_channels": "",
                "range_slices": "",
                "range_frames": "",
                "file": output_file,
                "unit": export_unit,
                "override_voxel_size": False,
                "n_resolution_levels": params.n_resolution_levels,
                "downscaling": 2,
                "tile_size_x": 256,
                "tile_size_y": 256,
                "n_threads": params.n_threads,
                "compression": params.compression,
                "compress_temp_files": False,
                "vox_size_xy_um": 0.0,
                "vox_size_z_um": 0.0,
            },
        ).get()
        result_path = str(output_file)
        print(f"Saved deconvolved OME-TIFF: {output_file}")
    else:
        print("Save skipped -- deconvolved sources are shown in BigDataViewer "
              "only (computed lazily as you browse).")

    # ---- 6. Sources cleanup ------------------------------------------------
    if not params.show_in_bdv:
        source_service.remove(_sac_array(deconvolved))
        source_service.remove(_sac_array(image_sources))
        source_service.remove(_sac_array(psf_sources))

    return result_path
