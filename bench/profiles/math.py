"""Measure production-shaped numerical expressions against ``unstd.math``.

Run ``python -m bench.profiles.math --output bench/reports/math/results.json``.
All fixtures are seeded synthetic data. Each paired wall-time batch is checked
outside its timed region, with alternating order. Allocation peaks use separate
tracemalloc passes and include conversion, validation and output allocation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
from statistics import median
import sys
import time
import tracemalloc
from typing import TYPE_CHECKING, Any, cast

import numpy as np

import unstd.math as ops


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from numpy.typing import NDArray


@dataclass(frozen=True)
class Workload:
    """One real expression, its replacement and an independently checked fixture."""

    name: str
    shape: str
    before: Callable[[], object]
    after: Callable[[], object]
    exact: bool = False


def _assert_outputs(before: object, after: object, *, exact: bool) -> None:
    """The complete numeric output, including shape, order and nonfinite values."""
    left = np.asarray(before)
    right = np.asarray(after)
    if left.shape != right.shape:
        message = f"shape mismatch: {left.shape} != {right.shape}"
        raise AssertionError(message)
    if exact:
        np.testing.assert_array_equal(left, right)
    else:
        rtol, atol = (4e-6, 2e-7) if left.dtype == np.float32 else (1e-12, 1e-12)
        np.testing.assert_allclose(left, right, rtol=rtol, atol=atol, equal_nan=True)


def _group_baseline(
    values: NDArray[np.floating[Any]], labels: NDArray[np.int64], groups: int
) -> NDArray[np.floating[Any]]:
    """The production per-domain mask/selection/mean loop, with absent groups zero."""
    output = np.zeros((groups, values.shape[1]), dtype=values.dtype)
    for group in range(groups):
        selected = values[labels == group]
        if selected.shape[0]:
            output[group] = selected.mean(axis=0)
    return output


def _frames_baseline(
    samples: NDArray[np.floating[Any]], length: int, hop: int
) -> NDArray[np.floating[Any]]:
    """The production integer-index matrix and advanced-indexing frame copy."""
    count = 1 + (samples.size - length) // hop
    indices = np.arange(length)[None, :] + hop * np.arange(count)[:, None]
    return cast("NDArray[np.floating[Any]]", samples[indices])


def _topk_before(values: NDArray[np.floating[Any]], k: int) -> object:
    """A stable full descending sort followed by a prefix slice."""
    return np.argsort(-values, kind="stable")[:k]


def _normalize_before(values: NDArray[np.floating[Any]], eps: float) -> object:
    """The production row norm/divide expression."""
    return values / (np.linalg.norm(values, axis=1, keepdims=True) + eps)


def _cosine_before(
    values: NDArray[np.floating[Any]], query: NDArray[np.floating[Any]], eps: float
) -> object:
    """The production full matrix normalization and matrix-vector product."""
    return (values / (np.linalg.norm(values, axis=1, keepdims=True) + eps)) @ (
        query / (np.linalg.norm(query) + eps)
    )


def _square_before(values: NDArray[np.floating[Any]]) -> object:
    """The production square temporary followed by a row reduction."""
    return np.mean(values**2, axis=1)


def _pearson_before(
    x: NDArray[np.floating[Any]], y: NDArray[np.floating[Any]]
) -> object:
    """The production general covariance-matrix correlation."""
    return float(np.corrcoef(x, y)[0, 1])


def _frame_energy_before(samples: NDArray[np.floating[Any]]) -> object:
    """Advanced-index frame construction followed by mean-square."""
    return np.mean(_frames_baseline(samples, 400, 160) ** 2, axis=1)


def _frame_energy_after(samples: NDArray[np.floating[Any]]) -> object:
    """View construction followed by the real mean-square helper."""
    return ops.mean_square(ops.frame(samples, 400, 160))


def _frame_energy_float32(samples: NDArray[np.floating[Any]]) -> object:
    """Frame energy with the caller explicitly retaining float32 arithmetic."""
    return ops.mean_square(ops.frame(samples, 400, 160), dtype="float32")


def _workloads() -> Iterator[Workload]:
    """Small and bulk shapes, retaining float32 and float64 conversion costs."""
    rng = np.random.default_rng(20261027)
    for dtype in (np.float32, np.float64):
        label = np.dtype(dtype).name
        for size in (64, 100_000):
            scores = cast(
                "NDArray[np.floating[Any]]", rng.normal(size=size).astype(dtype)
            )
            scores[::11] = 0.25  # ties are meaningful, including cutoff ties
            k = min(16, size)
            yield Workload(
                f"topk/{label}/{size}",
                f"{size} scores; k={k}; repeated ties",
                partial(_topk_before, scores, k),
                partial(ops.topk, scores, k),
                exact=True,
            )
        for rows, width in ((8, 64), (4096, 128)):
            values = cast(
                "NDArray[np.floating[Any]]",
                rng.normal(size=(rows, width)).astype(dtype),
            )
            values[0] = 0
            query = cast(
                "NDArray[np.floating[Any]]", rng.normal(size=width).astype(dtype)
            )
            eps = 1e-9
            yield Workload(
                f"normalize_rows/{label}/{rows}x{width}",
                f"{rows}x{width}; additive eps={eps}",
                partial(_normalize_before, values, eps),
                partial(ops.normalize_rows, values, eps=eps),
            )
            yield Workload(
                f"cosine_rows/{label}/{rows}x{width}",
                f"{rows}x{width}; zero row; additive eps={eps}",
                partial(_cosine_before, values, query, eps),
                partial(ops.cosine_rows, values, query, eps=eps),
            )
            yield Workload(
                f"mean_square/{label}/{rows}x{width}",
                f"{rows}x{width}; row mean-square used before dB conversion",
                partial(_square_before, values),
                partial(ops.mean_square, values),
            )
            if dtype is np.float32:
                yield Workload(
                    f"normalize_rows/{label}/{rows}x{width}/compute32",
                    f"{rows}x{width}; additive eps={eps}; explicit dtype=float32",
                    partial(_normalize_before, values, eps),
                    partial(ops.normalize_rows, values, eps=eps, dtype="float32"),
                )
                yield Workload(
                    f"cosine_rows/{label}/{rows}x{width}/compute32",
                    f"{rows}x{width}; zero row; explicit dtype=float32",
                    partial(_cosine_before, values, query, eps),
                    partial(ops.cosine_rows, values, query, eps=eps, dtype="float32"),
                )
                yield Workload(
                    f"mean_square/{label}/{rows}x{width}/compute32",
                    f"{rows}x{width}; explicit dtype=float32",
                    partial(_square_before, values),
                    partial(ops.mean_square, values, dtype="float32"),
                )
        for size in (50, 4096):
            x = cast("NDArray[np.floating[Any]]", rng.normal(size=size).astype(dtype))
            y = cast(
                "NDArray[np.floating[Any]]",
                (0.6 * x + rng.normal(size=size)).astype(dtype),
            )
            yield Workload(
                f"pearson/{label}/{size}",
                f"two {size}-element correlated series",
                partial(_pearson_before, x, y),
                partial(ops.pearson, x, y),
            )
        for rows, width, as_list in ((64, 64, True), (4096, 128, False)):
            matrix = cast(
                "NDArray[np.floating[Any]]",
                rng.normal(size=(rows, width)).astype(dtype),
            )
            weights = cast(
                "NDArray[np.floating[Any]]",
                rng.uniform(0.1, 1.0, size=rows).astype(dtype),
            )
            values_arg = matrix.tolist() if as_list else matrix
            weights_arg = weights.tolist() if as_list else weights
            shape = f"{rows}x{width}; {'Python lists' if as_list else 'ndarray'}"
            yield Workload(
                f"weighted_mean/{label}/{rows}x{width}/{'list' if as_list else 'array'}",
                shape,
                partial(np.average, values_arg, axis=0, weights=weights_arg),
                partial(ops.weighted_mean, values_arg, weights_arg),
            )
            if dtype is np.float32 and not as_list:
                yield Workload(
                    f"weighted_mean/{label}/{rows}x{width}/array/compute32",
                    f"{rows}x{width}; ndarray; explicit dtype=float32",
                    partial(np.average, values_arg, axis=0, weights=weights_arg),
                    partial(
                        ops.weighted_mean, values_arg, weights_arg, dtype="float32"
                    ),
                )
        for rows, width, groups in ((2000, 16, 4), (20_000, 32, 128)):
            values = cast(
                "NDArray[np.floating[Any]]",
                rng.normal(size=(rows, width)).astype(dtype),
            )
            labels = rng.integers(0, groups, size=rows, dtype=np.int64)
            yield Workload(
                f"grouped_mean/{label}/{rows}x{width}/{groups}",
                f"{rows}x{width}; {groups} dense groups",
                partial(_group_baseline, values, labels, groups),
                partial(ops.grouped_mean, values, labels, groups),
            )
        for size in (3200, 160_000):
            samples = cast(
                "NDArray[np.floating[Any]]", rng.normal(size=size).astype(dtype)
            )
            yield Workload(
                f"frame/{label}/{size}",
                f"{size} samples; length=400; hop=160; copy vs read-only view",
                partial(_frames_baseline, samples, 400, 160),
                partial(ops.frame, samples, 400, 160),
                exact=True,
            )
            yield Workload(
                f"frame_energy/{label}/{size}",
                f"{size} samples; framing plus downstream row mean-square",
                partial(_frame_energy_before, samples),
                partial(_frame_energy_after, samples),
            )
            if dtype is np.float32:
                yield Workload(
                    f"frame_energy/{label}/{size}/compute32",
                    f"{size} samples; framing plus energy; explicit dtype=float32",
                    partial(_frame_energy_before, samples),
                    partial(_frame_energy_float32, samples),
                )


def _iterations(operation: Callable[[], object]) -> int:
    """A bounded calibration for useful batch duration on small operations."""
    start = time.perf_counter_ns()
    for _ in range(3):
        operation()
    elapsed = max((time.perf_counter_ns() - start) / 3, 1)
    return max(1, min(256, int(2_000_000 / elapsed)))


def _batch(operation: Callable[[], object], count: int) -> tuple[float, object]:
    """Wall time per call, with the final complete output retained for checking."""
    result: object = None
    start = time.perf_counter_ns()
    for _ in range(count):
        result = operation()
    return (time.perf_counter_ns() - start) / count, result


def _peak(operation: Callable[[], object]) -> int:
    """A separate transient-allocation peak, including the returned output."""
    gc.collect()
    tracemalloc.start()
    try:
        result = operation()
        peak = tracemalloc.get_traced_memory()[1]
        del result
        return peak
    finally:
        tracemalloc.stop()


def _measure(workload: Workload, rounds: int) -> dict[str, Any]:
    """Verified paired batches, raw samples and separately measured allocations."""
    initial_before = np.asarray(workload.before())
    initial_after = np.asarray(workload.after())
    _assert_outputs(initial_before, initial_after, exact=workload.exact)
    max_error = (
        float(np.max(np.abs(initial_before - initial_after)))
        if initial_before.size
        else 0.0
    )
    baseline_dtype = str(initial_before.dtype)
    del initial_before, initial_after
    operations = {"before": workload.before, "after": workload.after}
    iterations = {
        name: _iterations(operation) for name, operation in operations.items()
    }
    samples: dict[str, list[float]] = {"before": [], "after": []}
    for round_index in range(rounds):
        order = ("before", "after") if round_index % 2 == 0 else ("after", "before")
        observed: dict[str, object] = {}
        for name in order:
            elapsed, output = _batch(operations[name], iterations[name])
            samples[name].append(elapsed)
            observed[name] = output
        _assert_outputs(observed["before"], observed["after"], exact=workload.exact)
    peaks = {name: _peak(operation) for name, operation in operations.items()}
    medians = {name: median(measured) for name, measured in samples.items()}
    return {
        "shape": workload.shape,
        "verified": True,
        "baseline_dtype": baseline_dtype,
        "max_absolute_error": max_error,
        "comparison": "exact"
        if workload.exact
        else "float32 baseline: rtol=4e-6, atol=2e-7; float64: rtol=atol=1e-12",
        "before": {
            "median_ns": medians["before"],
            "samples_ns_per_call": samples["before"],
            "iterations_per_batch": iterations["before"],
            "peak_traced_bytes": peaks["before"],
        },
        "after": {
            "median_ns": medians["after"],
            "samples_ns_per_call": samples["after"],
            "iterations_per_batch": iterations["after"],
            "peak_traced_bytes": peaks["after"],
        },
        "speedup": medians["before"] / medians["after"],
        "allocation_reduction": peaks["before"] / peaks["after"]
        if peaks["after"]
        else None,
    }


def _provenance() -> dict[str, Any]:
    """The actual imported source and measurement environment."""
    root = Path(__file__).resolve().parents[2]
    source = Path(ops.__file__).resolve()
    return {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "thread_settings": {
            name: os.environ.get(name)
            for name in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "source": source.relative_to(root).as_posix(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "benchmark_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def main() -> None:
    """Print and optionally persist measured evidence without dropping losing rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    arguments = parser.parse_args()
    if arguments.rounds < 3:
        parser.error("rounds must be at least three")
    provenance = _provenance()
    results = {
        workload.name: _measure(workload, arguments.rounds) for workload in _workloads()
    }
    if _provenance() != provenance:
        message = "source changed while benchmarks ran; rerun required"
        raise AssertionError(message)
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "method": {
            "clock": "perf_counter_ns",
            "rounds": arguments.rounds,
            "paired_order": "alternating",
            "fixture_seed": 20261027,
            "allocations": "separate tracemalloc pass including outputs and temporaries",
            "scope": "synthetic production-shaped expressions; not production deployment measurements",
        },
        "results": results,
    }
    payload = json.dumps(report, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
