"""Compare complete math workflows and edge shapes on macOS or Linux.

Run ``python -m bench.profiles.math_workflows --output /tmp/math-workflows.json``.
Every row retains equivalent-precision NumPy, the original package and current
package measurements, including all losses. ``--strict`` rejects material
regressions against either reference; the stated timing margin is not a claim
of zero overhead on arbitrary inputs or a substitute for production telemetry.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
import sys
import time
import tracemalloc
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from unstd import math as numeric


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from types import ModuleType

    from numpy.typing import NDArray


_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS = _ROOT / "bench/reports/math/edge-baseline.py"
_SEED = 20261027


@dataclass(frozen=True)
class Workflow:
    """Three complete calls with matching precision and caller behavior."""

    name: str
    shape: str
    numpy: Callable[[], object]
    previous: Callable[[], object]
    candidate: Callable[[], object]
    exact: bool = False


def _load_previous(path: Path) -> ModuleType:
    """Load the retained initial implementation without changing import paths."""
    spec = spec_from_file_location("_unstd_math_edge_baseline", path)
    if spec is None or spec.loader is None:
        message = f"cannot load previous math source: {path}"
        raise ValueError(message)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _topk_numpy(values: NDArray[Any], k: int) -> NDArray[np.intp]:
    """The stable production full sort, including the requested prefix."""
    return np.argsort(-values, kind="stable")[:k]


def _normalize_numpy(values: NDArray[Any]) -> NDArray[Any]:
    """The production additive row-norm expression at the input precision."""
    return cast(
        "NDArray[Any]", values / (np.linalg.norm(values, axis=1, keepdims=True) + 1e-9)
    )


def _cosine_numpy(values: NDArray[Any], query: NDArray[Any]) -> object:
    """The same additive norm policy on both sides of a gallery comparison."""
    return _normalize_numpy(values) @ (query / (np.linalg.norm(query) + 1e-9))


def _group_numpy(
    values: NDArray[Any], labels: NDArray[np.int64], groups: int
) -> NDArray[np.float64]:
    """The production mask loop, with the helper's float64/missing-group policy."""
    result = np.zeros((groups, values.shape[1]), dtype=np.float64)
    for group in range(groups):
        members = values[labels == group]
        if members.shape[0]:
            result[group] = members.mean(axis=0, dtype=np.float64)
    return result


def _frames_numpy(values: NDArray[Any], length: int, hop: int) -> NDArray[Any]:
    """The production complete-window advanced-index copy."""
    count = max(0, 1 + (values.size - length) // hop)
    index = np.arange(length)[None, :] + hop * np.arange(count)[:, None]
    return cast("NDArray[Any]", values[index])


def _retrieval(
    implementation: ModuleType | None,
    gallery: NDArray[Any],
    query: NDArray[Any],
    k: int,
) -> object:
    """Score an already normalized store and return ordered positions/scores."""
    scores = gallery @ query
    selected = (
        _topk_numpy(scores, k)
        if implementation is None
        else implementation.topk(scores, k)
    )
    return np.column_stack((selected, scores[selected]))


def _clustering(implementation: ModuleType | None, values: NDArray[Any]) -> object:
    """Normalize retained speaker embeddings and build their similarity matrix."""
    unit = (
        _normalize_numpy(values)
        if implementation is None
        else implementation.normalize_rows(values, eps=1e-9, dtype=values.dtype.name)
    )
    return unit @ unit.T


def _pool(
    implementation: ModuleType | None, values: list[list[float]], weights: list[float]
) -> object:
    """Convert generation embeddings, pool, normalize and return Python floats."""
    pooled = (
        np.average(values, axis=0, weights=weights)
        if implementation is None
        else implementation.weighted_mean(values, weights)
    )
    norm = np.linalg.norm(pooled)
    return (pooled / norm if norm else pooled).tolist()


def _energy(implementation: ModuleType | None, values: NDArray[Any]) -> object:
    """Frame an audio segment and produce its final dB energy measurements."""
    if implementation is None:
        frames = _frames_numpy(values, 400, 160)
        energy = np.mean(frames**2, axis=1)
    else:
        frames = implementation.frame(values, 400, 160)
        energy = implementation.mean_square(frames, dtype=values.dtype.name)
    return 10 * np.log10(energy + 1e-12)


def _workflows(previous: ModuleType) -> Iterator[Workflow]:
    """Tiny, bulk, strided, list and complete production-shaped fixtures."""
    rng = np.random.default_rng(_SEED)
    for dtype in (np.float32, np.float64):
        precision: numeric.Precision = "float32" if dtype is np.float32 else "float64"
        for size in (0, 1, 8, 64, 512):
            scores = rng.normal(size=size).astype(dtype)
            scores[::3] = 0.25
            for k in sorted({0, min(1, size), min(16, size), size}):
                yield Workflow(
                    f"topk/{precision}/n{size}/k{k}",
                    f"{size} contiguous scores; stable ties; k={k}",
                    partial(_topk_numpy, scores, k),
                    partial(previous.topk, scores, k),
                    partial(numeric.topk, scores, k),
                    exact=True,
                )
        for rows, width in ((1, 64), (8, 64), (4096, 128)):
            storage = rng.normal(size=(rows, width * 2)).astype(dtype)
            for layout in ("contiguous", "strided", "fortran"):
                values = storage[:, ::2]
                if layout == "contiguous":
                    values = np.ascontiguousarray(values)
                elif layout == "fortran":
                    values = np.asfortranarray(values)
                query = rng.normal(size=width).astype(dtype)
                weights = rng.uniform(0.1, 1, size=rows).astype(dtype)
                name = f"{precision}/{rows}x{width}/{layout}"
                shape = f"{rows}x{width}; {layout}; matching {precision} calculation"
                yield Workflow(
                    f"normalize_rows/{name}",
                    shape,
                    partial(_normalize_numpy, values),
                    partial(previous.normalize_rows, values, eps=1e-9, dtype=precision),
                    partial(numeric.normalize_rows, values, eps=1e-9, dtype=precision),
                )
                yield Workflow(
                    f"cosine_rows/{name}",
                    shape,
                    partial(_cosine_numpy, values, query),
                    partial(
                        previous.cosine_rows, values, query, eps=1e-9, dtype=precision
                    ),
                    partial(
                        numeric.cosine_rows, values, query, eps=1e-9, dtype=precision
                    ),
                )
                yield Workflow(
                    f"mean_square/{name}",
                    shape,
                    partial(lambda x: np.mean(x**2, axis=1), values),
                    partial(previous.mean_square, values, dtype=precision),
                    partial(numeric.mean_square, values, dtype=precision),
                )
                yield Workflow(
                    f"weighted_mean/{name}",
                    shape,
                    partial(np.average, values, axis=0, weights=weights),
                    partial(previous.weighted_mean, values, weights, dtype=precision),
                    partial(numeric.weighted_mean, values, weights, dtype=precision),
                )
        for rows, width, groups in ((32, 16, 4), (2000, 16, 4), (20_000, 32, 128)):
            storage = rng.normal(size=(rows, width * 2)).astype(dtype)
            labels = rng.integers(0, groups, size=rows, dtype=np.int64)
            for layout in ("contiguous", "strided"):
                values = storage[:, ::2]
                if layout == "contiguous":
                    values = np.ascontiguousarray(values)
                yield Workflow(
                    f"grouped_mean/{precision}/{rows}x{width}/g{groups}/{layout}",
                    f"{rows}x{width}; {groups} groups; {layout}; float64 output",
                    partial(_group_numpy, values, labels, groups),
                    partial(previous.grouped_mean, values, labels, groups),
                    partial(numeric.grouped_mean, values, labels, groups),
                )
        for size in (2, 50, 4096):
            raw_left = rng.normal(size=size)
            left = raw_left.astype(dtype)
            right = (raw_left * 0.6 + rng.normal(size=size)).astype(dtype)
            yield Workflow(
                f"pearson/{precision}/{size}",
                f"{size} observations; float64 correlation output",
                partial(lambda x, y: float(np.corrcoef(x, y)[0, 1]), left, right),
                partial(previous.pearson, left, right),
                partial(numeric.pearson, left, right),
            )
        for rows, width in ((64, 64), (4096, 128)):
            gallery = rng.normal(size=(rows, width)).astype(dtype)
            query = rng.normal(size=width).astype(dtype)
            k = min(16, rows)
            yield Workflow(
                f"workflow/retrieval/{precision}/{rows}x{width}",
                f"matrix-vector scoring plus stable top-{k} and score gather",
                partial(_retrieval, None, gallery, query, k),
                partial(_retrieval, previous, gallery, query, k),
                partial(_retrieval, numeric, gallery, query, k),
            )
        for rows in (8, 128):
            values = rng.normal(size=(rows, 64)).astype(dtype)
            yield Workflow(
                f"workflow/clustering/{precision}/{rows}x64",
                "speaker row normalization plus complete cosine matrix",
                partial(_clustering, None, values),
                partial(_clustering, previous, values),
                partial(_clustering, numeric, values),
            )
        for size in (1600, 3200, 160_000):
            samples = rng.normal(size=size).astype(dtype)
            yield Workflow(
                f"frame/{precision}/{size}",
                f"{size} samples; complete-frame index copy versus read-only view",
                partial(_frames_numpy, samples, 400, 160),
                partial(previous.frame, samples, 400, 160),
                partial(numeric.frame, samples, 400, 160),
                exact=True,
            )
            yield Workflow(
                f"workflow/frame_db/{precision}/{size}",
                f"{size} audio samples; complete frame+energy+dB workflow",
                partial(_energy, None, samples),
                partial(_energy, previous, samples),
                partial(_energy, numeric, samples),
            )
    for rows, width in ((1, 64), (8, 64), (64, 64)):
        values_list = rng.normal(size=(rows, width)).tolist()
        weights_list = rng.uniform(0.1, 1, size=rows).tolist()
        yield Workflow(
            f"workflow/list_pool/{rows}x{width}",
            "Python list conversion, weighted mean, L2 normalization and list output",
            partial(_pool, None, values_list, weights_list),
            partial(_pool, previous, values_list, weights_list),
            partial(_pool, numeric, values_list, weights_list),
        )


def _assert_output(actual: object, expected: object, exact: bool) -> float:
    """Check the complete shape and output, independently of timing success."""
    left, right = np.asarray(actual), np.asarray(expected)
    if left.shape != right.shape:
        message = f"shape differs: {left.shape} != {right.shape}"
        raise AssertionError(message)
    if exact:
        np.testing.assert_array_equal(left, right)
    else:
        tolerance = (
            5e-6 if left.dtype == np.float32 or right.dtype == np.float32 else 1e-12
        )
        np.testing.assert_allclose(
            left, right, rtol=tolerance, atol=tolerance, equal_nan=True
        )
    return float(np.max(np.abs(left - right))) if left.size else 0.0


def _iterations(operation: Callable[[], object]) -> int:
    """Bounded batches near five milliseconds, including tiny public calls."""
    start = time.perf_counter_ns()
    for _ in range(4):
        operation()
    elapsed = max((time.perf_counter_ns() - start) / 4, 1)
    return max(1, min(16384, int(5_000_000 / elapsed)))


def _batch(operation: Callable[[], object], count: int) -> tuple[float, object]:
    """Wall nanoseconds per complete call, retaining its final output."""
    result: object = None
    start = time.perf_counter_ns()
    for _ in range(count):
        result = operation()
    return (time.perf_counter_ns() - start) / count, result


def _peak(operation: Callable[[], object]) -> int:
    """A separate traced-allocation pass including the retained return value."""
    gc.collect()
    tracemalloc.start()
    try:
        result = operation()
        peak = tracemalloc.get_traced_memory()[1]
        del result
        return peak
    finally:
        tracemalloc.stop()


def _fixture_digest(workflow: Workflow) -> str:
    """Hash actual caller inputs and array metadata outside the timed region."""
    digest = sha256()
    seen: set[int] = set()
    pending: list[object] = [workflow.numpy, workflow.previous, workflow.candidate]
    while pending:
        value = pending.pop()
        if isinstance(value, partial):
            pending.extend(value.args)
            pending.extend(value.keywords.values())
        elif isinstance(value, np.ndarray):
            if id(value) in seen:
                continue
            seen.add(id(value))
            digest.update(
                repr(
                    (value.dtype.str, value.shape, value.strides, value.flags.writeable)
                ).encode()
            )
            if value.flags.c_contiguous:
                digest.update(memoryview(value).cast("B"))
            else:
                digest.update(value.tobytes())
        elif isinstance(value, list | tuple):
            digest.update(json.dumps(value, separators=(",", ":")).encode())
        elif value is None or isinstance(value, str | int | float):
            digest.update(repr(value).encode())
    return digest.hexdigest()


def _measure(
    workflow: Workflow, rounds: int, relative_margin: float, absolute_margin_ns: float
) -> dict[str, Any]:
    """Rotate three paired lanes; preserve losses rather than select best rounds."""
    operations = {
        "numpy": workflow.numpy,
        "previous": workflow.previous,
        "candidate": workflow.candidate,
    }
    input_digest = _fixture_digest(workflow)
    initial = {name: operation() for name, operation in operations.items()}
    errors = {
        name: _assert_output(value, initial["numpy"], workflow.exact)
        for name, value in initial.items()
    }
    del initial
    iterations = {
        name: _iterations(operation) for name, operation in operations.items()
    }
    samples: dict[str, list[float]] = {name: [] for name in operations}
    names = list(operations)
    for index in range(rounds):
        offset = index % len(names)
        order = names[offset:] + names[:offset]
        observed: dict[str, object] = {}
        for name in order:
            elapsed, output = _batch(operations[name], iterations[name])
            samples[name].append(elapsed)
            observed[name] = output
        for value in observed.values():
            _assert_output(value, observed["numpy"], workflow.exact)
    medians = {name: median(value) for name, value in samples.items()}
    thresholds = {
        name: medians[name] * (1 + relative_margin) + absolute_margin_ns
        for name in ("numpy", "previous")
    }
    lanes = {
        name: {
            "median_ns": medians[name],
            "samples_ns_per_call": samples[name],
            "iterations_per_batch": iterations[name],
            "peak_traced_bytes": _peak(operation),
            "max_absolute_error": errors[name],
        }
        for name, operation in operations.items()
    }
    if _fixture_digest(workflow) != input_digest:
        message = f"workflow mutated caller inputs: {workflow.name}"
        raise AssertionError(message)
    return {
        "shape": workflow.shape,
        "verified": True,
        "exact": workflow.exact,
        "input_sha256": input_digest,
        "input_nonmutation_verified": True,
        "lanes": lanes,
        "speedup_vs_numpy": medians["numpy"] / medians["candidate"],
        "speedup_vs_previous": medians["previous"] / medians["candidate"],
        "regression_threshold_ns": thresholds,
        "material_regression_vs": [
            name
            for name, threshold in thresholds.items()
            if medians["candidate"] > threshold
        ],
    }


def _provenance(previous: Path) -> dict[str, Any]:
    """Runtime, BLAS configuration and hashes of every math implementation file."""
    files = {
        "candidate": Path(numeric.__file__),
        "previous": previous,
        "benchmark": Path(__file__),
    }
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if name.startswith("unstd.math.") and isinstance(filename, str):
            files[name] = Path(filename)
    directory = _ROOT / "native/math"
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in {".c", ".h"}:
                files[path.relative_to(_ROOT).as_posix()] = path
    configuration = StringIO()
    with redirect_stdout(configuration):
        np.show_config()
    native = getattr(numeric, "_native", None)
    return {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "native_enabled": native is not None,
        "native_kernel_version": getattr(native, "KERNEL_VERSION", None),
        "numpy_configuration": configuration.getvalue(),
        "files_sha256": {
            name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()
        },
        "thread_settings": {
            name: os.environ.get(name)
            for name in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
    }


def _confirm_regressions(
    workflows: Sequence[Workflow],
    initial: Mapping[str, dict[str, Any]],
    *,
    rechecks: int,
    relative_margin: float,
    absolute_margin_ns: float,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    """Retain all evidence; reject only same-reference persistent regressions.

    Each originally failing case receives at most two 21-round confirmations
    with the original margins. A reference remains a failure only when it fails
    every confirmation. Cleared cases stop immediately; timings and initial
    results are never overwritten or reduced to a fastest attempt.
    """
    traces: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, list[str]] = {}
    for workflow in workflows:
        persistent: set[str] = set(initial[workflow.name]["material_regression_vs"])
        if not persistent:
            continue
        attempts: list[dict[str, Any]] = []
        for attempt in range(rechecks):
            result = _measure(workflow, 21, relative_margin, absolute_margin_ns)
            persistent.intersection_update(result["material_regression_vs"])
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "rounds": 21,
                    "measurement": result,
                    "persistent_references_after": sorted(persistent),
                }
            )
            if not persistent:
                break
        if attempts:
            traces[workflow.name] = attempts
        if persistent:
            failures[workflow.name] = sorted(persistent)
    return traces, failures


def main() -> None:
    """Write reproducible complete-workflow evidence, with an optional strict gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-source", type=Path, default=_PREVIOUS)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument(
        "--select", default="", help="Only include IDs containing this substring."
    )
    parser.add_argument("--relative-margin", type=float, default=0.15)
    parser.add_argument("--absolute-margin-ns", type=float, default=300)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument(
        "--rechecks",
        type=int,
        default=2,
        help="Confirm initially failing cases up to twice with 21 rounds and unchanged margins; 0 records exploration only.",
    )
    arguments = parser.parse_args()
    if (
        arguments.rounds < 3
        or not 0 <= arguments.relative_margin <= 0.30
        or arguments.absolute_margin_ns < 0
        or not math.isfinite(arguments.absolute_margin_ns)
        or not 0 <= arguments.rechecks <= 2
    ):
        parser.error(
            "rounds >= 3, relative margin in [0, .30], finite absolute margin >= 0 and rechecks in [0, 2] required"
        )
    previous = _load_previous(arguments.previous_source)
    workloads = [row for row in _workflows(previous) if arguments.select in row.name]
    if not workloads:
        parser.error("selection matched no workflows")
    native = getattr(numeric, "_native", None)
    native_path = getattr(native, "__file__", None)
    if arguments.require_native and (
        native is None
        or getattr(native, "KERNEL_VERSION", None) != 1
        or not isinstance(native_path, str)
        or not Path(native_path).is_file()
        or Path(native_path).stat().st_size == 0
    ):
        parser.error(
            "--require-native needs the loaded math kernel version 1 and its binary"
        )
    # Import-time setup and optional extension discovery must precede provenance.
    first_start = time.perf_counter_ns()
    first_result = workloads[0].candidate()
    first_ns = time.perf_counter_ns() - first_start
    _assert_output(first_result, workloads[0].numpy(), workloads[0].exact)
    del first_result
    provenance = _provenance(arguments.previous_source)
    results = {
        workflow.name: _measure(
            workflow,
            arguments.rounds,
            arguments.relative_margin,
            arguments.absolute_margin_ns,
        )
        for workflow in workloads
    }
    initial_regressions = {
        name: row["material_regression_vs"]
        for name, row in results.items()
        if row["material_regression_vs"]
    }
    rechecks, regressions = _confirm_regressions(
        workloads,
        results,
        rechecks=arguments.rechecks,
        relative_margin=arguments.relative_margin,
        absolute_margin_ns=arguments.absolute_margin_ns,
    )
    if _provenance(arguments.previous_source) != provenance:
        message = "source, extension or environment changed during measurement; rerun required"
        raise AssertionError(message)
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "method": {
            "clock": "perf_counter_ns",
            "target_batch_ns": 5_000_000,
            "native_required": arguments.require_native,
            "rounds": arguments.rounds,
            "paired_order": "cyclic rotation across all three lanes",
            "seed": _SEED,
            "precision": "NumPy and candidate compute at the same selected precision",
            "relative_regression_margin": arguments.relative_margin,
            "absolute_regression_margin_ns": arguments.absolute_margin_ns,
            "maximum_regression_rechecks": arguments.rechecks,
            "regression_recheck_rounds": 21,
            "regression_confirmation": "same reference must fail the initial measurement and every bounded confirmation; all attempts retained",
            "scope": "isolated synthetic expressions and complete production-shaped workflows",
            "guard": "candidate <= reference_median * (1 + relative_margin) + absolute_margin_ns; both references checked",
        },
        "results": results,
        "first_candidate_call_after_import": {
            "workflow": workloads[0].name,
            "elapsed_ns": first_ns,
            "scope": "first public call after module imports, before calibration; excludes process/import startup",
        },
        "material_regressions": regressions,
        "initial_material_regressions": initial_regressions,
        "rechecks": rechecks,
        "all_outputs_verified": True,
        "all_regression_guards_passed": not regressions,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "cases": len(results),
                "initial_regressions": initial_regressions,
                "regressions": regressions,
            }
        )
    )
    if arguments.strict and regressions:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
