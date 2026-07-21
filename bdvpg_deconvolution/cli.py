"""Command-line entry point.

Thin wrapper around :func:`bdvpg_deconvolution.pipeline.run` so the same core
can be called from a shell, a Makefile, or a Nextflow process.

This interface is deliberately **save-only**: it deconvolves and writes an
OME-TIFF. Viewing results in BigDataViewer is a job for the notebook, which can
keep the JVM and its windows alive -- a CLI process exits as soon as the work is
done, tearing the JVM (and any window) down with it.

    bdvpg-deconvolve --image raw.czi --psf psf.tif --out ./deconvolved
"""

from __future__ import annotations

import argparse
import sys

from .pipeline import DeconvolveParams, init_imagej, run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bdvpg-deconvolve",
        description="Tiled, lazy Richardson-Lucy GPU deconvolution "
                    "(BIOP / BigDataViewer-Playground) via PyImageJ.",
    )
    # I/O
    p.add_argument("--image", required=True, help="Multi-channel image to deconvolve")
    p.add_argument("--psf", required=True, help="Single-channel PSF image")
    p.add_argument("--out", dest="output_folder", required=True,
                   help="Output folder for <image>.ome.tiff")
    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite the output if it already exists")

    # Deconvolution parameters (defaults match the reference Fiji workflow)
    p.add_argument("--unit", default="MICROMETER",
                   choices=["MICROMETER", "MILLIMETER", "NANOMETER", "PIXEL"])
    p.add_argument("--output-pixel-type", default="Keep Pixel Type Of Original Image",
                   choices=["Keep Pixel Type Of Original Image", "Float"])
    p.add_argument("--block-size-x", type=int, default=256)
    p.add_argument("--block-size-y", type=int, default=256)
    p.add_argument("--block-size-z", type=int, default=64)
    p.add_argument("--overlap-size", type=int, default=16)
    p.add_argument("--iterations", dest="num_iterations", type=int, default=120)
    p.add_argument("--no-non-circulant", dest="non_circulant", action="store_false",
                   help="Disable non-circulant edge handling")
    p.add_argument("--regularization", dest="regularization_factor",
                   type=float, default=0.0)
    p.add_argument("--threads", dest="n_threads", type=int, default=10,
                   help="CPU-side workers feeding the GPU pool")
    p.add_argument("--compression", default="LZW",
                   choices=["LZW", "Uncompressed", "JPEG-2000",
                            "JPEG-2000 Lossy", "JPEG"])
    p.add_argument("--resolution-levels", dest="n_resolution_levels",
                   type=int, default=1)

    # Export sub-range selection. Zero-based, inclusive bounds; comma-separated
    # blocks; "start:end" or "start:step:end"; negative indices and the "end"
    # keyword count from the last element. Blank means everything.
    range_help = ("%s to export, zero-based, e.g. '0,2' or '0:3' or '0:2:end'. "
                  "Blank (default) exports all.")
    p.add_argument("--range-channels", default="",
                   help=range_help % "Channels")
    p.add_argument("--range-slices", default="",
                   help=range_help % "Z slices")
    p.add_argument("--range-frames", default="",
                   help=range_help % "Timepoints")

    # JVM / ImageJ
    p.add_argument("--mode", default="headless",
                   choices=["headless", "interactive"],
                   help="PyImageJ mode (default: headless). Nothing is displayed "
                        "either way; 'interactive' is an escape hatch for the "
                        "rare command that misbehaves under headless ImageJ.")
    p.add_argument("--max-heap", default=None,
                   help="JVM max heap, e.g. 32g")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    ij = init_imagej(mode=args.mode, max_heap=args.max_heap)

    params = DeconvolveParams(
        image_file=args.image,
        psf_file=args.psf,
        output_folder=args.output_folder,
        unit=args.unit,
        output_pixel_type=args.output_pixel_type,
        block_size_x=args.block_size_x,
        block_size_y=args.block_size_y,
        block_size_z=args.block_size_z,
        overlap_size=args.overlap_size,
        num_iterations=args.num_iterations,
        non_circulant=args.non_circulant,
        regularization_factor=args.regularization_factor,
        n_threads=args.n_threads,
        show_in_bdv=False,  # CLI is save-only; viewing belongs in the notebook
        save_output=True,
        compression=args.compression,
        n_resolution_levels=args.n_resolution_levels,
        overwrite=args.overwrite,
        range_channels=args.range_channels,
        range_slices=args.range_slices,
        range_frames=args.range_frames,
    )

    try:
        run(params, ij=ij)
    except Exception as exc:  # surface a clean error for pipelines
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
