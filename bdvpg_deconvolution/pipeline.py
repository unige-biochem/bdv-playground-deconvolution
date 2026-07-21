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
import re
import sys
import traceback
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
    "ch.epfl.biop:quick-start-czi-reader:0.3.0"
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


def hard_exit(code: int = 0) -> None:
    """Terminate the process immediately, JVM threads and all. Never returns.

    ImageJ initialises AWT even under ``mode="headless"``, leaving the
    non-daemon threads ``AWT-EventQueue-0`` (parked on an empty event queue)
    and ``AWT-Shutdown`` behind. Non-daemon threads keep the JVM alive, so a
    command-line process finishes its work, prints its results, and then hangs
    forever instead of exiting -- fatal for batch use, where the task would
    hold its slot indefinitely with the output already written.

    ``scyjava.shutdown_jvm()`` sometimes clears this and sometimes does not,
    and it can itself block, so entry points do not depend on it. ``os._exit``
    bypasses interpreter shutdown entirely and cannot be blocked by a thread.

    Because this skips JVM shutdown hooks, anything that must reach disk has
    to be flushed explicitly beforehand -- see
    :func:`bdvpg_deconvolution.gpu.set_pool`, which saves the ImageJ
    preferences rather than trusting them to be written at exit.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def entry_point(fn, *args, **kwargs) -> None:
    """Run a CLI ``main`` and terminate the process. Never returns.

    The hard exit has to happen inside ``main`` itself: a console-script
    wrapper that returns normally would hang in exactly the way
    :func:`hard_exit` exists to prevent.
    """
    try:
        code = fn(*args, **kwargs)
    except SystemExit as exc:
        code = exc.code
    except BaseException:
        traceback.print_exc()
        code = 1
    hard_exit(code if isinstance(code, int) else 0)


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


# --- Multi-series support ----------------------------------------------------
# A file can hold several images ("series" in Bio-Formats terms, e.g. one per
# stage position). ``getSourcesFromDataset`` flattens every channel of every
# series into one list, which would silently deconvolve unrelated images
# together, so instead we group them via the SourceService tree:
#
#     root > <dataset name> > "ImageName" > <series> > channels
#
# The tree is populated headlessly too -- SourceService.initialize() builds it
# with ``new SourceTree(this, context(), false)`` when the UI service reports
# headless, so only the JFrame is skipped, not the nodes.

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Turn a series name into a filename fragment.

    ``"Day4to5 - Position 5"`` becomes ``"Day4to5_-_Position_5"``.
    """
    cleaned = _INVALID_FILENAME_CHARS.sub("_", str(name))
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("._")
    return cleaned or "series"


def _series_nodes(source_service, dataset_name: str):
    """Return the per-series ``FilterNode``s of a dataset, or None.

    None means the expected tree layout was not found, in which case the caller
    should fall back to treating the dataset as a single series.
    """
    dataset_node = source_service.tree().root().child(dataset_name)
    if dataset_node is None:
        return None
    image_name_node = dataset_node.child("ImageName")
    if image_name_node is None:
        return None
    nodes = list(image_name_node.children())
    return nodes or None


def describe_series(source_service, dataset_name: str):
    """List ``(index, name, n_channels)`` for each series of an opened dataset.

    The index is the position in this listing -- it is what :attr:`
    DeconvolveParams.series` expects.
    """
    nodes = _series_nodes(source_service, dataset_name)
    if nodes is None:
        return []
    return [(i, str(n.name()), len(n.sources())) for i, n in enumerate(nodes)]


def _format_series_listing(image_file, series) -> str:
    width = max(len(name) for _, name, _ in series)
    lines = [f"  {i}  {name:<{width}}  ({n} channel{'s' if n != 1 else ''})"
             for i, name, n in series]
    return (
        f"'{Path(image_file).name}' contains {len(series)} series; "
        f"choose one with series=<index> (CLI: --series <index>):\n"
        + "\n".join(lines)
        + "\n\nEach series is written to its own file. The name defaults to "
          "<image>_<series name>.ome.tiff; set series_naming='index' "
          "(CLI: --series-naming index) for <image>_<index>.ome.tiff instead."
    )


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
    # Which series (image) of a multi-series file to process. None is fine for
    # single-series files; multi-series files require an explicit index.
    series: Optional[int] = None
    series_naming: str = "name"  # name | index -- suffix for multi-series output
    # CLIJ GPU pool, e.g. "0:2, 1:4". None leaves the persisted ImageJ
    # preference untouched. See bdvpg_deconvolution.gpu.
    gpu_pool: Optional[str] = None
    # Export sub-range selection (Kheops IntRangeParser syntax, "" = everything).
    # Applied at export time, on the deconvolved sources -- blocks outside the
    # selection are never computed, so a narrow range is genuinely cheaper.
    range_channels: str = ""
    range_slices: str = ""
    range_frames: str = ""


# --- The pipeline ------------------------------------------------------------

def run(params: DeconvolveParams, ij=None):
    """Run the deconvolution pipeline. Returns the output OME-TIFF path or None."""
    if ij is None:
        ij = init_imagej()

    # Must happen before anything touches the GPU: the pool is a lazy
    # singleton, built from this preference on first use.
    if params.gpu_pool is not None:
        from .gpu import format_pool_spec, set_pool  # avoids a circular import

        devices, workers = set_pool(params.gpu_pool)
        print(f"GPU pool: {format_pool_spec(devices, workers)} "
              f"({sum(workers)} GPU worker(s))")

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

    if params.save_output and params.output_folder is None:
        raise ValueError(
            "No output folder chosen: set output_folder, or disable "
            "save_output to only view the result."
        )
    if params.series_naming not in ("name", "index"):
        raise ValueError(
            f"series_naming must be 'name' or 'index', got "
            f"{params.series_naming!r}"
        )
    # The output name depends on which series is picked, so it is resolved
    # after the file is opened (opening is lazy, hence cheap).

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

    # ---- 1. Open the image and pick a series -------------------------------
    # The image is opened before the PSF so the series lookup cannot hit the
    # PSF's node should both files share a base name.
    all_image_sources = open_sources(image_file, image_name)
    if all_image_sources.isEmpty():
        raise RuntimeError(f"No source found in image file: {image_file}")

    series = describe_series(source_service, image_name)
    series_suffix = ""

    if len(series) > 1:
        if params.series is None:
            raise ValueError(_format_series_listing(image_file, series))
        if not 0 <= params.series < len(series):
            raise ValueError(
                f"series={params.series} is out of range.\n"
                + _format_series_listing(image_file, series)
            )
        index, series_name, _ = series[params.series]
        nodes = _series_nodes(source_service, image_name)
        image_sources = list(nodes[params.series].sources())
        series_suffix = ("_" + _sanitize_filename(series_name)
                         if params.series_naming == "name"
                         else f"_{index}")
        print(f"Opened '{image_name}' series {index} '{series_name}' : "
              f"{len(image_sources)} channel(s) "
              f"({len(series)} series in the file)")
    else:
        # Single series, or a tree layout we do not recognise: keep the flat
        # list, which is what every single-series file resolved to before.
        if params.series not in (None, 0):
            raise ValueError(
                f"series={params.series} was requested but "
                f"'{image_file.name}' holds a single series (use series=0 "
                f"or leave it unset)."
            )
        image_sources = list(all_image_sources)
        print(f"Opened '{image_name}' : {len(image_sources)} channel(s)")

    # ---- 1b. Open the PSF --------------------------------------------------
    # A multi-series PSF is unusual; as before, the first source is used.
    psf_sources = open_sources(psf_file, psf_name)
    if psf_sources.isEmpty():
        raise RuntimeError(f"No source found in PSF file: {psf_file}")

    psf_source = psf_sources.get(0)  # one PSF for all channels
    print(f"Opened PSF '{psf_name}' : {psf_sources.size()} source(s), "
          f"using the first one")

    # ---- 1c. Resolve and validate the output file --------------------------
    output_file = None
    if params.save_output:
        output_file = File(str(Path(params.output_folder).resolve()),
                           image_name + series_suffix + ".ome.tiff")
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
                "range_channels": params.range_channels,
                "range_slices": params.range_slices,
                "range_frames": params.range_frames,
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
        # every series, not just the selected one
        source_service.remove(_sac_array(all_image_sources))
        source_service.remove(_sac_array(psf_sources))

    return result_path
