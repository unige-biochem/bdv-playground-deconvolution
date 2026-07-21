"""Smoke test: boot the JVM with our endpoints and resolve every Java class /
service the pipeline needs. Does NOT run a deconvolution (no GPU/data needed)."""
import scyjava

from .pipeline import init_imagej, entry_point, _source_service, _jclass

CLASSES = [
    "java.io.File",
    "java.lang.Float",
    "org.apache.commons.io.FilenameUtils",
    "bdv.viewer.SourceAndConverter",
    "ch.epfl.biop.bdv.img.bioformats.command.DatasetFromBioFormatsCreateCommand",
    "ch.epfl.biop.command.process.deconvolve.SourcesDeconvolveCommand",
    "ch.epfl.biop.kheops.command.KheopsExportSourcesCommand",
    "sc.fiji.bdvpg.scijava.service.SourceService",
    "sc.fiji.bdvpg.service.SourceServices",
    "sc.fiji.bdvpg.scijava.service.tree.SourceTree",
    "sc.fiji.bdvpg.scijava.service.tree.FilterNode",
    "net.haesleinhuepf.clij.CLIJ",
    "net.haesleinhuepf.clijx.parallel.CLIJPoolOptions",
    "net.haesleinhuepf.clijx.parallel.CLIJxPool",
]


def main() -> None:
    """Console-script entry point. Terminates the process; never returns.

    That the process exits at all is part of what this smoke-tests: without
    the hard exit it hangs here forever with the work already done.
    """
    entry_point(_main)


def _main() -> int:
    ij = init_imagej(mode="headless")

    for c in CLASSES:
        _jclass(c)
        print("OK", c)

    svc = _source_service(ij)
    print("SourceService:", svc)
    print("has getSourcesFromDataset:", hasattr(svc, "getSourcesFromDataset"))

    # The multi-series lookup walks the SourceService tree; confirm it exists
    # headless (SourceTree is Swing-backed but built with makeGUI=false here).
    root = svc.tree().root()
    print("tree root (headless):", root, "children:", root.childCount())

    # GPU setup: enumerating devices does not allocate any context, so this is
    # safe to run anywhere -- it just reports what the pool would use.
    from .gpu import describe_gpu_setup
    print()
    print(describe_gpu_setup())
    print()

    # typed-array helper
    SAC = _jclass("bdv.viewer.SourceAndConverter")
    arr = scyjava.jarray(SAC, 0)
    print("jarray SourceAndConverter[] ok, len:", len(arr))

    print("\nALL RESOLVED")
    return 0


if __name__ == "__main__":
    main()
