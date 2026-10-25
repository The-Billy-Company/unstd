"""Paired binary packing evidence with owned-output and zero-copy controls.

Save the original ``src/unstd`` tree, then run this file unchanged on macOS and
Linux with the same baseline and candidate sources::

    python bench/profiles/pack_backend.py --compare-source /saved/before/src \
        --source src --rounds 7 --output /tmp/pack-backend.json

Workers select their source before importing the product. Alternating paired
rounds retain complete operation wall/CPU timings and exact output checks.
Cold imports are measured in separate minimal interpreters, including a scalar
round trip. Bulk typed-array cases are capability measurements, distinct from
the current Billy scalar/owned-list call shapes. No universal speedup is assumed.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
from hashlib import sha256
import importlib
import inspect
import json
from pathlib import Path
import platform
from statistics import median
import struct
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]
_COLD = """
import sys,time
sys.path.insert(0,sys.argv[1])
cpu=time.process_time_ns()
wall=time.perf_counter_ns()
from unstd.pack import binpack
assert binpack.unpack('>I',binpack.pack('>I',123456)) == (123456,)
elapsed=time.perf_counter_ns()-wall
elapsed_cpu=time.process_time_ns()-cpu
import json
print(json.dumps(dict(wall_ns=elapsed,cpu_ns=elapsed_cpu,
                     numpy_loaded='numpy' in sys.modules,source=binpack.__file__)))
"""


@dataclass(frozen=True)
class Case:
    """A complete operation with an independent output contract."""

    name: str
    run: Callable[[], Any]
    check: Callable[[Any], None]
    note: str


def _equal(expected: Any) -> Callable[[Any], None]:
    """Check both the owned result type and every expected value."""

    def check(actual: Any) -> None:
        if type(actual) is not type(expected) or actual != expected:
            message = "packing result differs from the independent struct oracle"
            raise AssertionError(message)

    return check


def _cases(codec: Any) -> list[Case]:
    """Build fixture values once, outside every timed operation."""
    import numpy as np

    values = [((index % 127) - 63) / 16 for index in range(256)]
    shape = codec.Struct("!256d")
    encoded = struct.pack("!256d", *values)
    f32 = struct.pack("<256f", *values)
    header = struct.pack(">I", 123456)
    rows = [values[:] for _ in range(200)]
    corpus = encoded * len(rows)
    cases = [
        Case(
            "billy/embedding-pack-256-list",
            lambda: shape.pack(*values),
            _equal(encoded),
            "Existing compiled !256d Struct.pack, including argument expansion",
        ),
        Case(
            "billy/embedding-unpack-256-owned-list",
            lambda: list(shape.unpack_from(encoded)),
            _equal(values),
            "Existing !256d Struct.unpack_from followed by owned Python list",
        ),
        Case(
            "billy/embedding-pack-page-200x256-lists",
            lambda: b"".join(shape.pack(*row) for row in rows),
            _equal(corpus),
            "Two hundred independent !256d rows, including join and argument expansion",
        ),
        Case(
            "billy/embedding-unpack-page-200x256-owned-lists",
            lambda: [list(row) for row in shape.iter_unpack(corpus)],
            _equal(rows),
            "Two hundred !256d records decoded to owned Python lists",
        ),
        Case(
            "billy/f32-unpack-256-owned-list",
            lambda: [float(value) for value in codec.unpack_array("<f4", f32)],
            _equal(values),
            "Existing float32 little-endian view followed by Python float conversion",
        ),
        Case(
            "billy/http-length-pack",
            partial(codec.pack, ">I", 123456),
            _equal(header),
            "Existing four-byte big-endian framing header",
        ),
        Case(
            "billy/http-length-unpack",
            partial(codec.unpack_from, ">I", header),
            _equal((123456,)),
            "Existing four-byte big-endian framing header",
        ),
    ]
    for count in (256, 51_200):
        for dtype, char in (("<f8", "d"), (">f8", "d"), ("<i4", "i"), (">i4", "i")):
            elements = [index % 127 - 63 for index in range(count)]
            expected = struct.pack(f"{dtype[0]}{count}{char}", *elements)
            source = np.asarray(elements, dtype=dtype)
            cases.append(
                Case(
                    f"portable/ndarray/{dtype}/{count}",
                    partial(codec.pack_array_portable, dtype, source),
                    _equal(expected),
                    "Exact one-dimensional typed ndarray; bytes output; no cached result",
                )
            )
            # Exercise byte-order conversion rather than only a matching buffer.
            target = (">" if dtype[0] == "<" else "<") + dtype[1:]
            swapped = struct.pack(f"{target[0]}{count}{char}", *elements)
            cases.append(
                Case(
                    f"portable/ndarray-swap/{dtype}-to-{target}/{count}",
                    partial(codec.pack_array_portable, target, source),
                    _equal(swapped),
                    "One-dimensional ndarray requiring opposite-endian bytes",
                )
            )
        float_values = [index / 16 for index in range(count)]
        doubles = np.asarray(float_values, dtype="<f8")
        expected = struct.pack(f"<{count}d", *float_values)
        cases.append(
            Case(
                f"portable/list/{count}",
                partial(codec.pack_array_portable, "<f8", float_values),
                _equal(expected),
                "Python float list; input snapshot and struct conversion included",
            )
        )
        if count == 256:
            cases.append(
                Case(
                    "portable/generator/256",
                    partial(
                        lambda source: codec.pack_array_portable(
                            "<f8", (value for value in source)
                        ),
                        float_values,
                    ),
                    _equal(expected),
                    "Fresh generator over Python floats; creation and complete consumption included",
                )
            )
        cases.append(
            Case(
                f"numpy/pack/{count}",
                partial(codec.pack_array, "<f8", doubles),
                _equal(expected),
                "Existing NumPy encode control; prebuilt typed array to owned bytes",
            )
        )

        def array_check(actual: Any, expected: bytes = expected) -> None:
            if (
                type(actual) is not np.ndarray
                or actual.dtype != np.dtype("<f8")
                or actual.ndim != 1
                or actual.flags.owndata
                or actual.flags.writeable
                or actual.tobytes() != expected
            ):
                message = (
                    "zero-copy decode does not match the independent struct oracle"
                )
                raise AssertionError(message)

        cases.append(
            Case(
                f"numpy/unpack-view/{count}",
                partial(codec.unpack_array, "<f8", expected),
                array_check,
                "Existing NumPy zero-copy decode control; ndarray output",
            )
        )
    float_elements = [index / 16 for index in range(51_200)]
    typed = array("d", float_elements)
    cases.append(
        Case(
            "portable/array-d/51200",
            partial(codec.pack_array_portable, "=f8", typed),
            _equal(struct.pack("=51200d", *float_elements)),
            "Stdlib array.array native doubles; independent of optional NumPy",
        )
    )
    return cases


def _batch(run: Callable[[], Any], count: int) -> tuple[int, int]:
    """Include allocation and ordinary reference-count result cleanup."""
    cpu = time.process_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(count):
        run()
    return time.perf_counter_ns() - wall, time.process_time_ns() - cpu


def _measure(case: Case, target_ns: int) -> dict[str, Any]:
    """Validate outside clocks and calibrate a substantial timed batch."""
    case.check(case.run())
    count = 1
    enabled = gc.isenabled()
    gc.disable()
    try:
        while True:
            elapsed, _ = _batch(case.run, count)
            if elapsed >= target_ns // 4 or count >= 1_000_000:
                break
            count *= 2
        count = max(1, min(1_000_000, int(count * target_ns / max(1, elapsed))))
        wall, cpu = _batch(case.run, count)
    finally:
        if enabled:
            gc.enable()
    case.check(case.run())
    return {
        "iterations": count,
        "wall_ns": wall,
        "cpu_ns": cpu,
        "wall_ns_per_call": wall / count,
        "cpu_ns_per_call": cpu / count,
        "note": case.note,
        "oracle_passed": True,
    }


def _source_hashes(source: Path) -> dict[str, str]:
    """Identify the entire pack implementation and its package import root."""
    paths = [source / "unstd" / "__init__.py"]
    paths.extend(sorted((source / "unstd" / "pack").rglob("*.py")))
    if not (source / "unstd" / "pack" / "binpack.py").is_file():
        message = f"source must contain unstd/pack/binpack.py: {source}"
        raise ValueError(message)
    return {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _worker(source: Path, selected: list[str], batch_ms: float) -> dict[str, Any]:
    """Load only the selected implementation in a fresh worker."""
    source = source.resolve()
    hashes = _source_hashes(source)
    if any(name == "unstd" or name.startswith("unstd.") for name in sys.modules):
        message = "product was imported before worker source selection"
        raise RuntimeError(message)
    sys.path.insert(0, str(source))
    codec = importlib.import_module("unstd.pack.binpack")
    if codec.__file__ is None:
        message = "selected pack implementation has no source file"
        raise RuntimeError(message)
    filename = Path(codec.__file__).resolve()
    if filename != source / "unstd" / "pack" / "binpack.py":
        message = "selected pack implementation was not imported"
        raise RuntimeError(message)
    cases = _cases(codec)
    unknown = set(selected) - {case.name for case in cases}
    if unknown:
        message = f"unknown cases: {sorted(unknown)}"
        raise ValueError(message)
    measurements = {
        case.name: _measure(case, int(batch_ms * 1_000_000))
        for case in cases
        if not selected or case.name in selected
    }
    if _source_hashes(source) != hashes:
        message = "pack source changed during measurement"
        raise RuntimeError(message)
    import numpy as np

    return {
        "source": str(source),
        "source_hashes": hashes,
        "imported_source": str(filename),
        "numpy_version": np.__version__,
        "signatures": {
            name: str(inspect.signature(getattr(codec, name)))
            for name in (
                "pack_array",
                "unpack_array",
                "pack_array_portable",
                "unpack_array_portable",
            )
        },
        "measurements": measurements,
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Retain alternating paired operations and separate fresh import timings."""
    sources = {
        "baseline": args.compare_source.resolve(),
        "candidate": args.source.resolve(),
    }
    hashes = {name: _source_hashes(path) for name, path in sources.items()}
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in sources}
    cold: dict[str, list[dict[str, Any]]] = {name: [] for name in sources}
    for index in range(args.rounds):
        order = (
            ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        )
        for name in order:
            command = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--worker",
                "--source",
                str(sources[name]),
                "--batch-ms",
                str(args.batch_ms),
            ]
            for case in args.case:
                command.extend(("--case", case))
            worker = subprocess.run(  # noqa: S603 — explicit local Python worker
                command, capture_output=True, text=True, check=True, timeout=120
            )
            sample = json.loads(worker.stdout)
            if sample["source_hashes"] != hashes[name]:
                message = f"{name} source changed across paired rounds"
                raise RuntimeError(message)
            samples[name].append(sample)
            started = time.perf_counter_ns()
            worker = subprocess.run(  # noqa: S603 — minimal fixed import experiment
                [sys.executable, "-I", "-c", _COLD, str(sources[name])],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            elapsed = time.perf_counter_ns() - started
            sample = json.loads(worker.stdout)
            if (
                Path(sample["source"]).resolve()
                != sources[name] / "unstd" / "pack" / "binpack.py"
            ):
                message = "cold interpreter loaded a different pack source"
                raise RuntimeError(message)
            sample["process_wall_ns"] = elapsed
            cold[name].append(sample)
        if (
            samples["baseline"][-1]["signatures"]
            != samples["candidate"][-1]["signatures"]
        ):
            message = "public array signatures changed between compared sources"
            raise RuntimeError(message)
    summary = {}
    for case in samples["baseline"][0]["measurements"]:
        before = [
            sample["measurements"][case]["wall_ns_per_call"]
            for sample in samples["baseline"]
        ]
        after = [
            sample["measurements"][case]["wall_ns_per_call"]
            for sample in samples["candidate"]
        ]
        before_cpu = [
            sample["measurements"][case]["cpu_ns_per_call"]
            for sample in samples["baseline"]
        ]
        after_cpu = [
            sample["measurements"][case]["cpu_ns_per_call"]
            for sample in samples["candidate"]
        ]
        summary[case] = {
            "baseline_median_wall_ns": median(before),
            "candidate_median_wall_ns": median(after),
            "median_wall_speedup": median(before) / median(after),
            "observed_conservative_wall_speedup": min(before) / max(after),
            "paired_wall_speedups": [
                left / right for left, right in zip(before, after, strict=True)
            ],
            "baseline_median_cpu_ns": median(before_cpu),
            "candidate_median_cpu_ns": median(after_cpu),
            "median_cpu_speedup": median(before_cpu) / median(after_cpu),
            "observed_conservative_cpu_speedup": min(before_cpu) / max(after_cpu),
        }
    cold_summary = {}
    for metric in ("wall_ns", "cpu_ns", "process_wall_ns"):
        before = [sample[metric] for sample in cold["baseline"]]
        after = [sample[metric] for sample in cold["candidate"]]
        cold_summary[metric] = {
            "baseline_median_ns": median(before),
            "candidate_median_ns": median(after),
            "median_speedup": median(before) / median(after),
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "rounds": args.rounds,
        "measurement": "complete call including output allocation and refcount cleanup; setup and oracle checks excluded; GC disabled during operation batches",
        "summary": summary,
        "samples": samples,
        "cold_import_summary": cold_summary,
        "cold_import_samples": cold,
    }


def main() -> None:
    """Write source-isolated comparison evidence and print concise timings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--batch-ms", type=float, default=20)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.batch_ms <= 0:
        parser.error("--batch-ms must be positive")
    if args.worker:
        print(json.dumps(_worker(args.source, args.case, args.batch_ms)))
        return
    if args.compare_source is None or args.output is None or args.rounds < 1:
        parser.error("--compare-source, --output, and positive --rounds are required")
    result = _run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for name, row in result["summary"].items():
        print(
            f"{name}: {row['baseline_median_wall_ns'] / 1000:.3f} -> "
            f"{row['candidate_median_wall_ns'] / 1000:.3f} us "
            f"({row['median_wall_speedup']:.3f}x)"
        )
    for name, row in result["cold_import_summary"].items():
        print(f"cold/{name}: {row['median_speedup']:.3f}x")


if __name__ == "__main__":
    main()
