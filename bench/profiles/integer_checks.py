"""Compare exact integer checks, optionally through a real consumer's close call.

Run ``python -m bench.profiles.integer_checks --output /tmp/integer-checks.json``.
``--consumer-source PATH`` loads the consumer's numerical contract module and
compares its legacy and current routes by changing backend availability outside
the timed region. Inputs and source files retain SHA-256 receipts.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import platform
from statistics import median
import sys
import time
import tracemalloc
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from unittest.mock import patch

import numpy as np
from numpy.typing import ArrayLike, NDArray

from unstd import math as numeric


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


type IntegerArray = NDArray[np.integer[Any]]


@runtime_checkable
class Consumer(Protocol):
    """The one checked numerical operation supplied by the optional consumer."""

    def close(
        self, actual: object, expected: object, *, rtol: object, atol: object
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class Fixture:
    """Frozen inputs with an independent arbitrary-precision comparison oracle."""

    name: str
    left: IntegerArray
    right: IntegerArray
    tolerance: int | IntegerArray

    def oracle(self) -> bool:
        """Independent arbitrary-precision scalar subtraction, outside timing."""
        if isinstance(self.tolerance, int):
            return all(
                abs(int(left) - int(right)) <= self.tolerance
                for left, right in zip(self.left.flat, self.right.flat, strict=True)
            )
        return all(
            abs(int(left) - int(right)) <= int(tolerance)
            for left, right, tolerance in zip(
                self.left.flat, self.right.flat, self.tolerance.flat, strict=True
            )
        )


def _fixtures() -> Iterator[Fixture]:
    for count in (8, 2500, 100_000, 1_000_000):
        left = np.arange(count, dtype=np.int64)
        yield Fixture(f"equal/{count}", left, left.copy(), 0)
        yield Fixture(f"distance-three/{count}", left, left + 3, 3)
    count = 4096
    left = np.asarray([-(2**63), -1, 2**63 - 1, 0] * (count // 4), dtype=np.int64)
    right = np.asarray([0, 2**64 - 2, 2**63, 0] * (count // 4), dtype=np.uint64)
    tolerance = np.asarray([2**63, 2**64 - 1, 1, 0] * (count // 4), dtype=np.uint64)
    yield Fixture("mixed-extrema/4096", left, right, tolerance)
    right[1] = 2**64 - 1
    yield Fixture(
        "mixed-carry-rejected/4096", left.copy(), right.copy(), tolerance.copy()
    )
    left = np.arange(8192, dtype=np.int64)[::-2].astype(">i8")
    right = (left + 1).astype(">u8")
    left.flags.writeable = right.flags.writeable = False
    yield Fixture("readonly-endian/4096", left, right, 1)
    left = np.arange(8192, dtype=np.int64)[::2]
    yield Fixture("strided/4096", left, left + 1, np.ones(left.shape, dtype=np.int64))


def _legacy(
    left: IntegerArray, right: IntegerArray, *, atol: int | IntegerArray
) -> bool:
    """The consumer's former exact integer arithmetic, including unused magnitude."""
    relative = np.asarray(0)
    absolute = np.asarray(atol)
    magnitude = right.astype(object)
    difference = np.asarray(left.astype(object) - right.astype(object), dtype=object)
    limit = absolute if relative == 0 else absolute + relative * np.abs(magnitude)
    return bool(
        np.isfinite(left).all()
        and np.isfinite(right).all()
        and np.all(np.abs(difference) <= limit)
    )


def _consumer(consumer: Consumer, fixture: Fixture) -> bool:
    try:
        result = consumer.close(
            fixture.left, fixture.right, rtol=0, atol=fixture.tolerance
        )
    except ValueError as error:
        if "Numerical comparison failed its declared tolerances" not in str(error):
            raise
        return False
    if result is not None:
        message = "Consumer close must return None on success"
        raise TypeError(message)
    return True


@contextmanager
def _route(*, legacy: bool) -> Iterator[None]:
    with patch.object(numeric, "within", None if legacy else numeric.within):
        yield


def _batch(operation: Callable[[], bool], count: int) -> tuple[float, float]:
    start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
    for _ in range(count):
        operation()
    cpu, elapsed = time.thread_time_ns() - cpu_start, time.perf_counter_ns() - start
    return elapsed / count, cpu / count


def _count(operation: Callable[[], bool]) -> int:
    fastest = min(_batch(operation, 1)[0] for _ in range(3))
    return max(1, min(65536, int(5_000_000 / max(fastest, 1))))


def _peak(operation: Callable[[], bool]) -> int:
    tracemalloc.start()
    try:
        operation()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _measure(
    before: Callable[[], bool], after: Callable[[], bool], expected: bool, rounds: int
) -> dict[str, object]:
    operations = before, after
    counts = []
    peaks = []
    for index, operation in enumerate(operations):
        with _route(legacy=index == 0):
            assert operation() is expected
            counts.append(_count(operation))
            peaks.append(_peak(operation))
    samples: tuple[list[float], list[float]] = [], []
    cpu_samples: tuple[list[float], list[float]] = [], []
    for pair in range(rounds):
        for index in (pair % 2, 1 - pair % 2):
            with _route(legacy=index == 0):
                wall, cpu = _batch(operations[index], counts[index])
                assert operations[index]() is expected
            samples[index].append(wall)
            cpu_samples[index].append(cpu)
    before_ns, after_ns = (median(values) for values in samples)
    return {
        "iterations": counts,
        "wall_samples_ns": samples,
        "thread_cpu_samples_ns": cpu_samples,
        "before_ns": before_ns,
        "after_ns": after_ns,
        "speedup": before_ns / after_ns,
        "target_100x_met": before_ns >= 100 * after_ns,
        "peak_python_bytes": peaks,
        "accepted": expected,
    }


def _load_consumer(path: Path) -> Consumer:
    spec = spec_from_file_location("_unstd_integer_consumer", path)
    if spec is None or spec.loader is None:
        message = "Consumer must be a readable Python numerical contract module"
        raise ValueError(message)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(module, Consumer):
        message = (
            "Consumer must expose the close(actual, expected, *, rtol, atol) contract"
        )
        raise TypeError(message)
    return module


def _prove_consumer(consumer: Consumer, fixture: Fixture, expected: bool) -> int:
    """Count actual public helper entries in a separate untimed consumer call."""
    implementation = numeric.within
    calls = 0

    def observing(actual: ArrayLike, reference: ArrayLike, *, atol: ArrayLike) -> bool:
        nonlocal calls
        calls += 1
        return implementation(actual, reference, atol=atol)

    with patch.object(numeric, "within", observing):
        assert _consumer(consumer, fixture) is expected
    assert calls == 1, "Consumer did not reach the public exact comparison helper"
    return calls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--consumer-source", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("--rounds must be at least three")
    sources = {"profile": Path(__file__), "unstd.math": Path(numeric.__file__)}
    if args.consumer_source:
        sources["consumer_numeric_module"] = args.consumer_source
    source_hashes = {
        label: sha256(path.read_bytes()).hexdigest() for label, path in sources.items()
    }
    consumer = _load_consumer(args.consumer_source) if args.consumer_source else None
    rows: list[dict[str, object]] = []
    for fixture in _fixtures():
        digest = sha256()
        for value in (fixture.left, fixture.right, np.asarray(fixture.tolerance)):
            digest.update(str((value.dtype.str, value.shape)).encode())
            digest.update(value.tobytes())
        expected = fixture.oracle()
        row: dict[str, object] = {
            "case": fixture.name,
            "input_sha256": digest.hexdigest(),
            "shape": fixture.left.shape,
            "primitive": _measure(
                partial(_legacy, fixture.left, fixture.right, atol=fixture.tolerance),
                partial(
                    numeric.within, fixture.left, fixture.right, atol=fixture.tolerance
                ),
                expected,
                args.rounds,
            ),
        }
        if consumer is not None:
            row["consumer_within_calls"] = _prove_consumer(consumer, fixture, expected)
            operation = partial(_consumer, consumer, fixture)
            row["consumer_close"] = _measure(
                operation, operation, expected, args.rounds
            )
        rows.append(row)
        print(fixture.name)
    assert source_hashes == {
        label: sha256(path.read_bytes()).hexdigest() for label, path in sources.items()
    }, "Measured source changed during the comparison"
    report = {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "method": "Alternating paired wall/thread-CPU batches; medians; independent Python-int oracle before and after timing; tracemalloc in a separate pass; backend availability changes outside timing.",
        "limits": "Synthetic CPU workloads on one shared host; no kernel startup, model, network or end-to-end application latency claim. Nonzero-relative and floating tolerances stay on the consumer's legacy path.",
        "source_sha256": source_hashes,
        "results": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
