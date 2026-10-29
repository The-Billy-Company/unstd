"""Compare exact integer checks, optionally through a real consumer's close call.

Run ``python -m bench.profiles.integer_checks --output /tmp/integer-checks.json``.
``--consumer-source PATH`` loads the consumer's numerical contract module and
compares object arithmetic, the retained first pass and the current helper.
Bindings change outside timing; input and source bytes retain SHA-256 receipts.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import partial
from hashlib import sha256
from importlib.machinery import ModuleSpec, SourceFileLoader
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import platform
from statistics import median
import sys
import time
import tracemalloc
from types import BuiltinFunctionType
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable
from unittest.mock import patch

import numpy as np
from numpy.typing import ArrayLike, NDArray

from unstd import math as numeric


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import NotImplementedType


type IntegerArray = NDArray[np.integer[Any]]

_FIRST_PASS = (
    Path(__file__).resolve().parents[1]
    / "reports/math/integer-checks-first-pass.py.txt"
)
_PREVIOUS_DIGEST = "9afe09473744b2470bb1195af5c486cf264ca3b22060e77a036f1727fae2652d"
_PREVIOUS_REVISION = "3876cbbaf6727c4d3e0c9664c1f4c3f441202101"


@runtime_checkable
class Within(Protocol):
    """The exact comparison contract shared by retained and current sources."""

    def __call__(
        self, actual: ArrayLike, expected: ArrayLike, *, atol: ArrayLike
    ) -> bool: ...


@runtime_checkable
class NativeWithin(Protocol):
    """The private checked-buffer entry point and its explicit decline result."""

    def __call__(
        self, actual: object, expected: object, atol: object, /
    ) -> bool | NotImplementedType: ...


@runtime_checkable
class Consumer(Protocol):
    """The one checked numerical operation supplied by the optional consumer."""

    def close(
        self, actual: object, expected: object, *, rtol: object, atol: object
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class Lane:
    """One bound public implementation and its optional compiled backend."""

    name: str
    operation: Callable[[], bool]
    implementation: Within | None
    portable: bool = False


class Samples(TypedDict):
    """Raw measurements retained once and shared by matching comparisons."""

    iterations: int
    wall_samples_ns: list[float]
    thread_cpu_samples_ns: list[float]
    peak_python_bytes: int
    accepted: bool


class RouteProof(TypedDict):
    """Separate public-entry, successful-native and declined-native counts."""

    within_calls: int
    native_calls: int
    native_declines: int


@dataclass(frozen=True, slots=True)
class Fixture:
    """Frozen inputs with an independent arbitrary-precision comparison oracle."""

    name: str
    left: IntegerArray
    right: IntegerArray
    tolerance: int | IntegerArray
    consumer_skip_reason: str | None = None

    def __post_init__(self) -> None:
        """Read-only inputs keep comparison implementations from borrowing writes."""
        for value in (self.left, self.right, self.tolerance):
            if isinstance(value, np.ndarray):
                value.flags.writeable = False

    def fingerprint(self) -> str:
        """Exact input storage receipt, checked again outside timed batches."""
        digest = sha256()
        for value in (self.left, self.right, np.asarray(self.tolerance)):
            digest.update(str((value.dtype.str, value.shape)).encode())
            digest.update(value.tobytes())
        return digest.hexdigest()

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
    for count in (0, 1, 2, 8, 16, 17, 32, 33, 128, 256, 512, 2500, 100_000, 1_000_000):
        left: IntegerArray = np.arange(count, dtype=np.int64)
        yield Fixture(
            f"equal/{count}",
            left,
            left.copy(),
            0,
            "Consumer requires nonempty operands" if not count else None,
        )
        if not count:
            continue
        yield Fixture(f"distance-three/{count}", left, left + 3, 3)
    count = 4096
    for position, index in (("first", 0), ("middle", count // 2), ("last", count - 1)):
        left = np.arange(count, dtype=np.int64)
        right = left + 3
        right[index] += 1
        yield Fixture(f"mismatch-{position}/{count}", left, right, 3)
    for dtype in ("int8", "uint8", "int16", "uint16", "int32", "uint32"):
        left = (np.arange(count) % 64).astype(dtype)
        yield Fixture(f"narrow-{dtype}/{count}", left, left + 1, 1)
    for left_dtype, right_dtype in (("int16", "int64"), ("uint8", "int64")):
        left = (np.arange(count) % 64).astype(left_dtype)
        right = (left + 1).astype(right_dtype)
        yield Fixture(f"mixed-{left_dtype}-{right_dtype}/{count}", left, right, 1)
    for layout in ("matrix", "fortran", "reversed-matrix"):
        left = np.arange(count, dtype=np.int32).reshape(64, 64)
        if layout == "fortran":
            left = np.asfortranarray(left)
        elif layout == "reversed-matrix":
            left = left[::-1, ::-1]
        yield Fixture(
            f"{layout}/{count}", left, left + 1, np.ones(left.shape, dtype=np.int32)
        )
    left = np.asarray([-(2**63), -1, 2**63 - 1, 0] * (count // 4), dtype=np.int64)
    right = np.asarray([0, 2**64 - 2, 2**63, 0] * (count // 4), dtype=np.uint64)
    tolerance = np.asarray([2**63, 2**64 - 1, 1, 0] * (count // 4), dtype=np.uint64)
    yield Fixture("mixed-extrema/4096", left, right, tolerance)
    rejected = right.copy()
    rejected[1] = 2**64 - 1
    yield Fixture("mixed-carry-rejected/4096", left.copy(), rejected, tolerance.copy())
    left = np.arange(8192, dtype=np.int64)[::-2].astype(">i8")
    right = (left + 1).astype(">u8")
    left.flags.writeable = right.flags.writeable = False
    yield Fixture("readonly-endian/4096", left, right, 1)
    left = np.arange(100_000, dtype=np.int64).astype(">i8")
    yield Fixture("same-kind-endian/100000", left, (left + 3).astype(">i8"), 3)
    left = np.arange(8192, dtype=np.int64)[::2]
    yield Fixture("strided/4096", left, left + 1, np.ones(left.shape, dtype=np.int64))
    left = np.full(8, -(2**63), dtype=np.int64)
    right = np.full(8, 2**64 - 1, dtype=np.uint64)
    distance = 2**64 + 2**63 - 1
    reason = "Consumer rejects absolute tolerances stored as Python object integers"
    yield Fixture("wide-tolerance-accepted/8", left, right, distance, reason)
    yield Fixture("wide-tolerance-rejected/8", left, right, distance - 1, reason)


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
def _route(implementation: Within | None, *, portable: bool = False) -> Iterator[None]:
    with ExitStack() as owners:
        owners.enter_context(patch.object(numeric, "within", implementation))
        if portable:
            owners.enter_context(patch.object(numeric, "_native", None))
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
    lanes: tuple[Lane, ...],
    expected: bool,
    rounds: int,
    *,
    fixture: Fixture,
) -> dict[str, Samples]:
    fingerprint = fixture.fingerprint()
    result: dict[str, Samples] = {}
    for lane in lanes:
        with _route(lane.implementation, portable=lane.portable):
            assert lane.operation() is expected
            result[lane.name] = {
                "iterations": _count(lane.operation),
                "peak_python_bytes": _peak(lane.operation),
                "wall_samples_ns": [],
                "thread_cpu_samples_ns": [],
                "accepted": expected,
            }
            assert fixture.fingerprint() == fingerprint, "Comparison mutated its inputs"
    for pair in range(rounds):
        # Rotate all four positions rather than always timing one backend first.
        for offset in range(len(lanes)):
            lane = lanes[(pair + offset) % len(lanes)]
            samples = result[lane.name]
            with _route(lane.implementation, portable=lane.portable):
                wall, cpu = _batch(lane.operation, samples["iterations"])
                assert lane.operation() is expected
                assert fixture.fingerprint() == fingerprint, (
                    "Comparison mutated its inputs"
                )
            samples["wall_samples_ns"].append(wall)
            samples["thread_cpu_samples_ns"].append(cpu)
    return result


def _comparison(before: Samples, after: Samples) -> dict[str, object]:
    """Matching comparisons reuse the same raw observations for every lane."""
    before_ns, after_ns = (
        median(sample["wall_samples_ns"]) for sample in (before, after)
    )
    return {
        "iterations": [sample["iterations"] for sample in (before, after)],
        "wall_samples_ns": [sample["wall_samples_ns"] for sample in (before, after)],
        "thread_cpu_samples_ns": [
            sample["thread_cpu_samples_ns"] for sample in (before, after)
        ],
        "before_ns": before_ns,
        "after_ns": after_ns,
        "speedup": before_ns / after_ns,
        "paired_speedup_samples": [
            left / right
            for left, right in zip(
                before["wall_samples_ns"], after["wall_samples_ns"], strict=True
            )
        ],
        "target_100x_met": before_ns >= 100 * after_ns,
        "peak_python_bytes": [
            sample["peak_python_bytes"] for sample in (before, after)
        ],
        "accepted": after["accepted"],
    }


def _lanes(
    fixture: Fixture, previous: Within, current: Within, consumer: Consumer | None
) -> tuple[Lane, ...]:
    """Bind the same four implementations at the primitive or consumer seam."""
    if consumer is None:
        legacy = partial(_legacy, fixture.left, fixture.right, atol=fixture.tolerance)
        first = partial(previous, fixture.left, fixture.right, atol=fixture.tolerance)
        now = partial(current, fixture.left, fixture.right, atol=fixture.tolerance)
    else:
        legacy = first = now = partial(_consumer, consumer, fixture)
    return (
        Lane("legacy", legacy, None),
        Lane("previous", first, previous),
        Lane("portable", now, current, portable=True),
        Lane("current", now, current),
    )


def _load_consumer(path: Path) -> Consumer:
    spec = spec_from_file_location("_unstd_integer_consumer", path)
    if spec is None or spec.loader is None:
        message = "Consumer must be a readable Python numerical contract module"
        raise ValueError(message)
    module = module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102 — explicitly selected local consumer, compiled from recorded bytes
    if not isinstance(module, Consumer):
        message = (
            "Consumer must expose the close(actual, expected, *, rtol, atol) contract"
        )
        raise TypeError(message)
    return module


def _load_previous(path: Path) -> Within:
    """An independent retained implementation, never borrowing current globals."""
    if path.resolve() == _FIRST_PASS.resolve():
        assert sha256(path.read_bytes()).hexdigest() == _PREVIOUS_DIGEST, (
            "Retained first-pass source differs from its captured revision"
        )
    name = "_unstd_integer_first_pass"
    source = str(path.resolve())
    spec = ModuleSpec(name, SourceFileLoader(name, source), origin=source)
    spec.has_location = True
    module = module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102 — retained local implementation, compiled from recorded bytes
    implementation: object = getattr(module, "within", None)
    if not isinstance(implementation, Within):
        message = "Previous source must define within(actual, expected, *, atol)"
        raise TypeError(message)
    return implementation


def _prove_consumer(
    consumer: Consumer | None,
    fixture: Fixture,
    expected: bool,
    implementation: Within,
    *,
    portable: bool = False,
) -> RouteProof:
    """Observe actual public and compiled helper results outside timed calls."""
    calls = 0
    native_calls = 0
    native_declines = 0

    def observing(actual: ArrayLike, expected: ArrayLike, *, atol: ArrayLike) -> bool:
        nonlocal calls
        calls += 1
        return implementation(actual, expected, atol=atol)

    with _route(observing, portable=portable), ExitStack() as owners:
        backend = numeric._native
        native: object = getattr(backend, "within", None)
        if isinstance(native, NativeWithin):

            def observing_native(
                actual: object, reference: object, atol: object, /
            ) -> bool | NotImplementedType:
                nonlocal native_calls, native_declines
                result = native(actual, reference, atol)
                if result is NotImplemented:
                    native_declines += 1
                elif isinstance(result, bool):
                    native_calls += 1
                else:
                    message = "Native comparison must return bool or NotImplemented"
                    raise TypeError(message)
                return result

            owners.enter_context(patch.object(backend, "within", observing_native))
        if consumer is None:
            assert (
                numeric.within(fixture.left, fixture.right, atol=fixture.tolerance)
                is expected
            )
        else:
            assert _consumer(consumer, fixture) is expected
    assert calls == 1, "Consumer did not reach the public exact comparison helper"
    return {
        "within_calls": calls,
        "native_calls": native_calls,
        "native_declines": native_declines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--consumer-source", type=Path)
    parser.add_argument("--previous-source", type=Path, default=_FIRST_PASS)
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument(
        "--case", action="append", default=[], help="Case name glob; repeatable"
    )
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("--rounds must be at least three")
    backend = numeric._native
    available = isinstance(getattr(backend, "within", None), BuiltinFunctionType)
    if args.require_native and not available:
        parser.error("Current installation does not provide the native within kernel")
    sources = {
        "profile": Path(__file__),
        "unstd.math": Path(numeric.__file__),
        "first_pass": args.previous_source,
        "native_math_source": Path(__file__).resolve().parents[2]
        / "native/math/src/adapter.c",
    }
    binary: object = getattr(backend, "__file__", None)
    if isinstance(binary, str):
        sources["native_math_binary"] = Path(binary)
    if args.consumer_source:
        sources["consumer_numeric_module"] = args.consumer_source
    source_hashes = {
        label: sha256(path.read_bytes()).hexdigest() for label, path in sources.items()
    }
    consumer = _load_consumer(args.consumer_source) if args.consumer_source else None
    previous = _load_previous(args.previous_source)
    current = numeric.within
    rows: list[dict[str, object]] = []
    required_native = {"equal/1000000", "distance-three/1000000"}
    covered_native: set[str] = set()
    for fixture in _fixtures():
        if args.case and not any(
            fnmatchcase(fixture.name, pattern) for pattern in args.case
        ):
            continue
        expected = fixture.oracle()
        row: dict[str, object] = {
            "case": fixture.name,
            "input_sha256": fixture.fingerprint(),
            "shape": fixture.left.shape,
        }
        surfaces: list[tuple[str, Consumer | None]] = [("primitive", None)]
        if consumer is not None and fixture.consumer_skip_reason is not None:
            row["consumer_skipped"] = fixture.consumer_skip_reason
        elif consumer is not None:
            surfaces.append(("consumer_close", consumer))
        for surface, selected_consumer in surfaces:
            lanes = _lanes(fixture, previous, current, selected_consumer)
            measured = _measure(lanes, expected, args.rounds, fixture=fixture)
            for prefix, before, after in (
                ("", "legacy", "current"),
                ("previous_", "previous", "current"),
                ("portable_", "previous", "portable"),
                ("native_vs_portable_", "portable", "current"),
            ):
                row[prefix + surface] = _comparison(measured[before], measured[after])
            proofs = {
                lane.name: _prove_consumer(
                    selected_consumer,
                    fixture,
                    expected,
                    lane.implementation,
                    portable=lane.portable,
                )
                for lane in lanes
                if lane.implementation is not None
            }
            row[surface + "_routes"] = proofs
            if args.require_native and fixture.name in required_native:
                assert proofs["current"]["native_calls"] == 1, (
                    f"Required native result was not reached: {surface}/{fixture.name}"
                )
                assert proofs["current"]["native_declines"] == 0
                covered_native.add(fixture.name)
            if selected_consumer is not None:
                row["consumer_within_calls"] = proofs["current"]["within_calls"]
                row["previous_consumer_within_calls"] = proofs["previous"][
                    "within_calls"
                ]
        rows.append(row)
        print(fixture.name)
    if not rows:
        parser.error("No fixtures matched the requested case selection")
    if args.require_native and covered_native != required_native:
        parser.error(
            "Native qualification requires equal/1000000 and distance-three/1000000"
        )
    assert source_hashes == {
        label: sha256(path.read_bytes()).hexdigest() for label, path in sources.items()
    }, "Measured source changed during the comparison"
    report = {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "method": "Rotating four-lane wall/thread-CPU batches; shared samples compare object legacy, retained first pass, current portable and current native. Independent Python-int oracle, unchanged input receipts and separate allocations. Bindings and native state change outside timing; public and successful/declined C entries are proved separately.",
        "limits": "Synthetic CPU workloads on one shared host; no kernel startup, model, network or end-to-end application latency claim. Nonzero-relative and floating tolerances stay on the consumer's legacy path.",
        "source_sha256": source_hashes,
        "native_within_available": available,
        "native_kernel_version": getattr(backend, "KERNEL_VERSION", None),
        "first_pass_revision": (
            _PREVIOUS_REVISION
            if args.previous_source.resolve() == _FIRST_PASS.resolve()
            else None
        ),
        "results": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
