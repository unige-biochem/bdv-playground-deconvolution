"""Smoke test: boot the JVM with our endpoints and resolve every Java class /
service the pipeline needs. Does NOT run a deconvolution (no GPU/data needed)."""
import scyjava

from .pipeline import init_imagej, _source_service, _jclass

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
]


def main() -> int:
    ij = init_imagej(mode="headless")

    for c in CLASSES:
        _jclass(c)
        print("OK", c)

    svc = _source_service(ij)
    print("SourceService:", svc)
    print("has getSourcesFromDataset:", hasattr(svc, "getSourcesFromDataset"))

    # typed-array helper
    SAC = _jclass("bdv.viewer.SourceAndConverter")
    arr = scyjava.jarray(SAC, 0)
    print("jarray SourceAndConverter[] ok, len:", len(arr))

    print("\nALL RESOLVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
