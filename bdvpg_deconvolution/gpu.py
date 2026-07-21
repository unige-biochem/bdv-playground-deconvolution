"""GPU pool configuration and inspection.

Deconvolution runs on a pool of CLIJ contexts spread over the available GPUs.
The pool is described by a *pool specification* -- ``"0:2, 1:4"`` means 2
contexts on device 0 and 4 on device 1, i.e. 6 GPU workers in total.

Two things about this configuration are easy to get wrong, so they are worth
stating plainly:

* **It is persistent, global ImageJ state.** ``CLIJPoolOptions.set()`` writes
  ``ij.Prefs`` under the key
  ``net.haesleinhuepf.clijx.parallel.CLIJPoolOptions.pool_specification``,
  exactly as the Fiji dialog does. It outlives the process and is shared with
  any other ImageJ/Fiji tool on the machine.
* **It is read once per JVM.** ``CLIJxPool.getInstance()`` builds a lazy
  singleton from the preference the first time a pool is needed; changing the
  preference afterwards has no effect until the next process.

Hence :func:`set_pool` must be called before any deconvolution runs, which is
what :func:`bdvpg_deconvolution.pipeline.run` does with its ``gpu_pool``
parameter.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from .pipeline import _jclass, entry_point, init_imagej

_POOL_SPEC_RE = re.compile(r"^\s*\d+\s*:\s*\d+\s*(,\s*\d+\s*:\s*\d+\s*)*$")

_POOL_SPEC_HELP = (
    "Expected 'device:workers' pairs separated by commas, e.g. '0:1' for one "
    "worker on GPU 0, or '0:2, 1:4' for 2 workers on GPU 0 and 4 on GPU 1."
)


def _pool_options():
    return _jclass("net.haesleinhuepf.clijx.parallel.CLIJPoolOptions")


def _pool():
    return _jclass("net.haesleinhuepf.clijx.parallel.CLIJxPool")


def available_devices() -> list[str]:
    """Names of the OpenCL devices CLIJ can see, indexed as in a pool spec.

    Enumerating does not allocate any GPU context.
    """
    CLIJ = _jclass("net.haesleinhuepf.clij.CLIJ")
    return [str(n) for n in CLIJ.getAvailableDeviceNames()]


def current_pool() -> tuple[list[int], list[int]]:
    """The persisted pool configuration as ``(devices, workers_per_device)``."""
    opts = _pool_options()
    return [int(d) for d in opts.getDevices()], [int(t) for t in opts.getThreads()]


def format_pool_spec(devices, workers) -> str:
    return ", ".join(f"{d}:{w}" for d, w in zip(devices, workers))


def validate_pool_spec(spec: str, devices: Optional[list[str]] = None) -> None:
    """Raise ValueError if ``spec`` is malformed or names a missing device.

    ``devices`` defaults to the machine's actual device list; pass it
    explicitly to validate without querying OpenCL.
    """
    if not _POOL_SPEC_RE.match(spec or ""):
        raise ValueError(f"Invalid GPU pool specification {spec!r}. {_POOL_SPEC_HELP}")

    if devices is None:
        devices = available_devices()
    if not devices:
        # Nothing to check against; let CLIJ produce the authoritative failure.
        return

    requested = {int(pair.split(":")[0]) for pair in spec.split(",")}
    missing = sorted(d for d in requested if d >= len(devices))
    if missing:
        listing = "\n".join(f"  {i}  {name}" for i, name in enumerate(devices))
        raise ValueError(
            f"GPU pool specification {spec!r} refers to device(s) "
            f"{', '.join(map(str, missing))}, but only {len(devices)} "
            f"device(s) are available:\n{listing}"
        )


def set_pool(spec: str, validate: bool = True) -> tuple[list[int], list[int]]:
    """Persist a pool specification and return what was stored.

    Warns on stderr if the pool singleton was already built in this JVM, in
    which case the new setting only takes effect in the next process.
    """
    if validate:
        validate_pool_spec(spec)

    if _pool().isIntanceSet():
        print(
            "WARNING: the GPU pool was already created in this process; the "
            "new configuration is persisted but will only apply to the next "
            "run.",
            file=sys.stderr,
        )

    _pool_options().set(spec)

    # ij.Prefs normally reaches disk when ImageJ shuts down, and our entry
    # points terminate the process without running shutdown hooks (see
    # pipeline.hard_exit), so save it here instead of losing the setting.
    _jclass("ij.Prefs").savePreferences()

    return current_pool()


def describe_gpu_setup() -> str:
    """Human-readable summary of the devices and the configured pool."""
    lines = []

    devices = available_devices()
    if devices:
        lines.append(f"Available OpenCL devices ({len(devices)}):")
        lines += [f"  {i}  {name}" for i, name in enumerate(devices)]
    else:
        lines.append("Available OpenCL devices: none detected")

    device_idx, workers = current_pool()
    lines.append("")
    lines.append(f"Configured pool: {format_pool_spec(device_idx, workers)}")
    for d, w in zip(device_idx, workers):
        name = devices[d] if d < len(devices) else "UNAVAILABLE DEVICE"
        lines.append(f"  device {d}  {w} worker{'s' if w != 1 else ''}  {name}")
    lines.append(f"  total GPU workers: {sum(workers)}")
    return "\n".join(lines)


# --- Entry point -------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bdvpg-gpu-pool",
        description="Show or set the CLIJ GPU pool used for deconvolution. "
                    "With no argument it only reports the current setup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The setting is written to the ImageJ preferences, so it "
               "persists across runs and is shared with Fiji.",
    )
    p.add_argument("spec", nargs="?", default=None,
                   help="Pool specification, e.g. '0:2, 1:4' for 2 workers on "
                        "GPU 0 and 4 on GPU 1. Omit to only print the setup.")
    p.add_argument("--probe", action="store_true",
                   help="Also build the pool and print its details. This "
                        "allocates real GPU contexts, so it is a genuine test "
                        "that the configuration works.")
    p.add_argument("--max-heap", default=None, help="JVM max heap, e.g. 32g")
    return p


def main(argv=None) -> None:
    """Console-script entry point. Terminates the process; never returns."""
    entry_point(_main, argv)


def _main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Fail on an obviously malformed spec before paying for a JVM boot. The
    # device-existence half of the check needs OpenCL, so it happens later.
    if args.spec is not None and not _POOL_SPEC_RE.match(args.spec):
        print(f"ERROR: Invalid GPU pool specification {args.spec!r}. "
              f"{_POOL_SPEC_HELP}", file=sys.stderr)
        return 2

    init_imagej(mode="headless", max_heap=args.max_heap)

    try:
        if args.spec is not None:
            devices, workers = set_pool(args.spec)
            print(f"GPU pool set to: {format_pool_spec(devices, workers)}\n")

        print(describe_gpu_setup())

        if args.probe:
            print("\nBuilding the pool (allocating GPU contexts)...")
            pool = _pool().getInstance()
            print(pool.getDetails())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    main()
