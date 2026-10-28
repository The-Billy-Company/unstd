"""Checked production-shaped workloads for the focused NumPy math operations."""

from __future__ import annotations

from functools import partial
import math
from typing import TYPE_CHECKING, Any

from bench.speeds import Case


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import ExitStack

    from numpy.typing import NDArray


def _array_check(
    expected: NDArray[Any],
) -> Callable[[object], None]:
    """Check shape, dtype and values against a separately calculated result."""
    import numpy as np

    def check(actual: object) -> None:
        if not isinstance(actual, np.ndarray):
            message = f"expected ndarray, got {type(actual).__name__}"
            raise TypeError(message)
        if actual.dtype != expected.dtype or actual.shape != expected.shape:
            message = f"expected {expected.dtype} {expected.shape}, got {actual.dtype} {actual.shape}"
            raise AssertionError(message)
        np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-13)

    return check


def cases(stack: ExitStack, *, comparison: bool = False) -> list[Case]:
    """Build bounded independent oracles; older revisions can omit the package."""
    del stack
    try:
        from unstd import math as numeric
    except ImportError:
        if comparison:
            return []
        raise

    import numpy as np

    rows = [
        [float((row * 11 + column * 7) % 37 - 18) for column in range(8)]
        for row in range(64)
    ]
    values = np.asarray(rows, dtype=np.float64)
    # Keep all fixture calculations independent of the public helper and its
    # NumPy reduction recipe, so a changed kernel cannot change its own oracle.
    squared = [math.fsum(value * value for value in row) for row in rows]
    norms = [math.sqrt(value) for value in squared]
    normalized = np.asarray(
        [[value / norm for value in row] for row, norm in zip(rows, norms, strict=True)]
    )
    query = np.asarray([float(i - 3) for i in range(8)])
    query_norm = math.sqrt(math.fsum(value * value for value in query))
    similarities = np.asarray(
        [
            math.fsum(value * other for value, other in zip(row, query, strict=True))
            / (norm * query_norm)
            for row, norm in zip(rows, norms, strict=True)
        ]
    )
    means = np.asarray([value / 8 for value in squared])
    weights = np.asarray([float(1 + row % 3) for row in range(64)])
    total_weight = math.fsum(weights)
    weighted = np.asarray(
        [
            math.fsum(
                row[column] * weight for row, weight in zip(rows, weights, strict=True)
            )
            / total_weight
            for column in range(8)
        ]
    )
    labels = np.asarray([row % 4 for row in range(64)], dtype=np.intp)
    grouped = np.asarray(
        [
            [
                math.fsum(rows[index][column] for index in range(group, 64, 4)) / 16
                for column in range(8)
            ]
            for group in range(4)
        ]
        + [[0.0] * 8]
    )
    scores = values[:, 0]
    ranked = np.asarray(
        sorted(range(len(scores)), key=lambda index: (-scores[index], index))[:8],
        dtype=np.intp,
    )
    x, y = values[:, 0], values[:, 1]
    x_mean, y_mean = math.fsum(x) / len(x), math.fsum(y) / len(y)
    x_centered, y_centered = (
        [value - x_mean for value in x],
        [value - y_mean for value in y],
    )
    correlation = math.fsum(
        a * b for a, b in zip(x_centered, y_centered, strict=True)
    ) / math.sqrt(
        math.fsum(value * value for value in x_centered)
        * math.fsum(value * value for value in y_centered)
    )

    def pearson_check(actual: object) -> None:
        if not isinstance(actual, float) or not math.isclose(
            actual, correlation, rel_tol=1e-13, abs_tol=1e-13
        ):
            message = f"expected correlation {correlation!r}, got {actual!r}"
            raise AssertionError(message)

    samples = np.arange(1024, dtype=np.float32)
    frames = np.asarray(
        [list(range(start, start + 64)) for start in range(0, 1024 - 64 + 1, 32)],
        dtype=np.float32,
    )
    check_frames = _array_check(frames)

    def frame_check(actual: object) -> None:
        check_frames(actual)
        if not isinstance(actual, np.ndarray) or actual.flags.writeable:
            message = "framing must return a read-only array"
            raise AssertionError(message)
        if not np.shares_memory(actual, samples):
            message = "framing copied the source samples"
            raise AssertionError(message)

    integers = np.asarray([0, 2**63 - 1, -1, -(2**63)] * 16, dtype=np.int64)
    reference = np.asarray([0, 2**63, 2**64 - 2, 0] * 16, dtype=np.uint64)
    tolerance = np.asarray([0, 1, 2**64 - 1, 2**63] * 16, dtype=np.uint64)
    accepted = all(
        abs(int(left) - int(right)) <= int(limit)
        for left, right, limit in zip(integers, reference, tolerance, strict=True)
    )

    def within_check(actual: object) -> None:
        if actual is not accepted:
            message = f"expected exact integer comparison {accepted}, got {actual!r}"
            raise AssertionError(message)

    return [
        Case(
            "unstd.math.topk",
            partial(numeric.topk, scores, 8),
            _array_check(ranked),
            "Select eight descending positions from 64 tied scores, retaining input order for ties.",
        ),
        Case(
            "unstd.math.normalize_rows",
            partial(numeric.normalize_rows, values),
            _array_check(normalized),
            "Normalize 64 eight-dimensional feature rows, checked against scalar Euclidean norms.",
        ),
        Case(
            "unstd.math.cosine_rows",
            partial(numeric.cosine_rows, values, query),
            _array_check(similarities),
            "Compare 64 eight-dimensional embedding rows to one query without materializing normalized rows.",
        ),
        Case(
            "unstd.math.mean_square",
            partial(numeric.mean_square, values),
            _array_check(means),
            "Compute mean square for 64 eight-sample frames, checked against scalar sums of squares.",
        ),
        Case(
            "unstd.math.pearson",
            partial(numeric.pearson, x, y),
            pearson_check,
            "Compute scalar Pearson correlation for two strided 64-value feature columns.",
        ),
        Case(
            "unstd.math.weighted_mean",
            partial(numeric.weighted_mean, values, weights),
            _array_check(weighted),
            "Aggregate 64 eight-dimensional rows with positive per-row weights and scalar reference sums.",
        ),
        Case(
            "unstd.math.grouped_mean",
            partial(numeric.grouped_mean, values, labels, 5),
            _array_check(grouped),
            "Aggregate 64 eight-dimensional rows into five label groups, including an empty group.",
        ),
        Case(
            "unstd.math.frame",
            partial(numeric.frame, samples, 64, 32),
            frame_check,
            "Create 31 overlapping 64-sample read-only float32 frame views without copying samples.",
        ),
        Case(
            "unstd.math.within",
            partial(numeric.within, integers, reference, atol=tolerance),
            within_check,
            "Compare 64 mixed signed/unsigned integers against exact per-element integer tolerances without rounding or wrapping.",
        ),
    ]
