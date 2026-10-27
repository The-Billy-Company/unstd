"""Specialized NumPy reductions for retrieval, grouped data and audio.

Install ``unstd[math]`` to use these operations. Numeric calculations return
float64 arrays by default, accept real numeric array-likes, and never mutate inputs.
``topk`` preserves the input's ordering precision; ``frame`` preserves its dtype
and returns a read-only view. No pandas, SciPy or native build is required.
"""

from __future__ import annotations

import math as _math
import operator
from typing import TYPE_CHECKING, Any, Literal, cast


if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import ArrayLike, NDArray

    _HAVE_NUMPY = True
else:
    try:
        import numpy as np

        _HAVE_NUMPY = True
    except ImportError:
        _HAVE_NUMPY = False


type FloatArray = NDArray[np.float32 | np.float64]
type RealArray = NDArray[np.integer[Any] | np.floating[Any]]
type Precision = Literal["float32", "float64"]

__all__ = [
    "cosine_rows",
    "frame",
    "grouped_mean",
    "mean_square",
    "normalize_rows",
    "pearson",
    "topk",
    "weighted_mean",
]


def _real(values: ArrayLike, ndim: int) -> RealArray:
    """A real numeric array of the required dimension, without an eager copy."""
    if not _HAVE_NUMPY:
        msg = "unstd.math requires NumPy; install with: pip install 'unstd[math]'"
        raise ImportError(msg)
    array = np.asarray(values)
    if array.ndim != ndim:
        msg = f"expected {ndim}-dimensional values, got {array.ndim}"
        raise ValueError(msg)
    if array.dtype.kind not in "iuf":
        msg = "values must have a real integer or floating dtype"
        raise TypeError(msg)
    return array


def _float(values: ArrayLike, ndim: int) -> FloatArray:
    """A float64 calculation buffer, promoting integers before arithmetic."""
    return cast("FloatArray", _real(values, ndim).astype(np.float64, copy=False))


def _count(value: int, name: str, minimum: int = 0) -> int:
    """An index-compatible integer with a checked lower bound."""
    if isinstance(value, bool | np.bool_):
        msg = f"{name} must be an integer, not bool"
        raise TypeError(msg)
    result = operator.index(value)
    if result < minimum:
        msg = f"{name} must be at least {minimum}"
        raise ValueError(msg)
    return result


def _epsilon(eps: float) -> float:
    """A finite nonnegative additive norm floor."""
    if not _math.isfinite(eps) or eps < 0:
        msg = "eps must be finite and nonnegative"
        raise ValueError(msg)
    return eps


def _precision(dtype: Precision) -> type[np.float32 | np.float64]:
    """The explicitly chosen multiplication, accumulation and output precision."""
    if dtype == "float64":
        return np.float64
    if dtype == "float32":
        return np.float32
    msg = "dtype must be 'float32' or 'float64'"
    raise ValueError(msg)


def _norms(rows: RealArray, dtype: Precision) -> FloatArray:
    """Row L2 norms without materializing a squared matrix."""
    return cast(
        "FloatArray",
        np.sqrt(
            np.einsum("ij,ij->i", rows, rows, dtype=_precision(dtype), casting="unsafe")
        ),
    )


def topk(values: ArrayLike, k: int) -> NDArray[np.intp]:
    """Indices of the largest *k* values, descending with first-position ties.

    ``0 <= k <= len(values)``; NaNs are rejected and infinities are ordered.
    Integers retain exact ordering, including uint64 and signed minima. A
    partition selects the cutoff, then only the selected positions are sorted.
    Time is O(N + K log K); inputs are never sorted or modified in place.
    """
    array = _real(values, 1)
    count = _count(k, "k")
    if count > array.size:
        msg = "k exceeds the number of values"
        raise ValueError(msg)
    if array.dtype.kind == "f" and np.isnan(array).any():
        msg = "topk does not accept NaN scores"
        raise ValueError(msg)
    if count == 0:
        return np.empty(0, dtype=np.intp)
    if count == 1:
        return np.asarray([np.argmax(array)], dtype=np.intp)
    if count == array.size:
        selected = np.arange(array.size, dtype=np.intp)
    else:
        cutoff = np.partition(array, array.size - count)[array.size - count]
        above = np.flatnonzero(array > cutoff)
        tied = np.flatnonzero(array == cutoff)[: count - above.size]
        selected = np.concatenate((above, tied))
    # Complement reverses integer order without overflowing signed minima or
    # rounding uint64 to float. Lexsort repairs ties across the cutoff buckets.
    keys = (
        np.bitwise_not(array[selected])
        if array.dtype.kind in "iu"
        else -array[selected]
    )
    return selected[np.lexsort((selected, keys))]


def normalize_rows(
    values: ArrayLike, *, eps: float = 0.0, dtype: Precision = "float64"
) -> FloatArray:
    """Each matrix row divided by ``norm(row) + eps``; zero rows stay zero.

    *eps* is finite and nonnegative. The additive floor matches embedding
    pipelines using ``X / (norm(X, axis=1, keepdims=True) + eps)``. Float64
    promotion happens before squaring; nonfinite data follows NumPy arithmetic.
    ``dtype="float32"`` keeps float32 multiplication, accumulation and output
    for pipelines that already use that precision.
    """
    rows = _real(values, 2)
    denominator = _norms(rows, dtype) + _epsilon(eps)
    zero = denominator == 0
    denominator[zero] = 1
    out = np.divide(
        rows, denominator[:, None], dtype=_precision(dtype), casting="unsafe"
    )
    out[zero] = 0
    return cast("FloatArray", out)


def cosine_rows(
    values: ArrayLike,
    query: ArrayLike,
    *,
    eps: float = 0.0,
    dtype: Precision = "float64",
) -> FloatArray:
    """Cosines against each matrix row without allocating a normalized matrix.

    The denominator is ``(row_norm + eps) * (query_norm + eps)``. Zero vectors
    score zero. *query* must be one-dimensional and match the matrix width.
    A store already containing unit vectors should use ``values @ query``.
    ``dtype="float32"`` chooses float32 multiplication, accumulation and output.
    """
    rows = _real(values, 2)
    precision = _precision(dtype)
    vector = _real(query, 1).astype(precision, copy=False)
    if rows.shape[1] != vector.size:
        msg = "query length must match the matrix width"
        raise ValueError(msg)
    floor = _epsilon(eps)
    norm = float(
        np.sqrt(np.einsum("i,i->", vector, vector, dtype=precision, casting="unsafe"))
    )
    denominator = (_norms(rows, dtype) + floor) * (norm + floor)
    dots = np.einsum("ij,j->i", rows, vector, dtype=precision, casting="unsafe")
    out = np.zeros(rows.shape[0], dtype=precision)
    np.divide(dots, denominator, out=out, where=denominator != 0)
    return out


def mean_square(values: ArrayLike, *, dtype: Precision = "float64") -> FloatArray:
    """Mean squared value per matrix row, without a squared-matrix temporary.

    A zero-width matrix returns one NaN per row. Float64 accumulation avoids
    integer overflow and float32 accumulation error. Apply ``sqrt`` for RMS or
    ``10 * log10(result + 1e-12)`` for the production audio energy convention.
    ``dtype="float32"`` chooses float32 multiplication, accumulation and output.
    """
    rows = _real(values, 2)
    precision = _precision(dtype)
    if rows.shape[1] == 0:
        return np.full(rows.shape[0], np.nan, dtype=precision)
    return cast(
        "FloatArray",
        np.einsum("ij,ij->i", rows, rows, dtype=precision, casting="unsafe")
        / rows.shape[1],
    )


def pearson(x: ArrayLike, y: ArrayLike) -> float:
    """Pearson correlation of equal-length vectors, using centered reductions.

    Fewer than two observations or a constant vector returns NaN. Callers that
    use zero for an undefined correlation must retain that domain policy.
    Nonfinite values propagate; no observations are silently omitted.
    """
    left = _float(x, 1)
    right = _float(y, 1)
    if left.size != right.size:
        msg = "vectors must have equal lengths"
        raise ValueError(msg)
    if left.size < 2:
        return float("nan")
    # Subtract a nearby origin before the mean: a rounded mean of an exactly
    # constant large vector can otherwise leave a spurious nonzero variance.
    left = left - left[0]
    right = right - right[0]
    left -= left.mean()
    right -= right.mean()
    square_left = float(np.einsum("i,i->", left, left))
    square_right = float(np.einsum("i,i->", right, right))
    if square_left == 0 or square_right == 0:
        return float("nan")
    cross = float(np.einsum("i,i->", left, right))
    result = cross / _math.sqrt(square_left) / _math.sqrt(square_right)
    return float(np.clip(result, -1.0, 1.0))


def weighted_mean(
    values: ArrayLike, weights: ArrayLike, *, dtype: Precision = "float64"
) -> FloatArray:
    """A weighted mean of matrix rows without a weighted-matrix temporary.

    One finite nonnegative weight is required per row, with a finite positive
    total. Negative weights are deliberately excluded from this pooling API.
    Conversion of lists is part of the call; both inputs remain unchanged.
    ``dtype="float32"`` chooses float32 multiplication, accumulation and output.
    """
    rows = _real(values, 2)
    weight = _float(weights, 1)
    precision = _precision(dtype)
    if rows.shape[0] != weight.size:
        msg = "one weight is required per matrix row"
        raise ValueError(msg)
    total = float(weight.sum(dtype=precision))
    if (
        not np.isfinite(weight).all()
        or (weight < 0).any()
        or not _math.isfinite(total)
        or total <= 0
    ):
        msg = "weights must be finite and nonnegative with a finite positive total"
        raise ValueError(msg)
    weight = weight.astype(precision, copy=False)
    return cast(
        "FloatArray",
        np.einsum("ij,i->j", rows, weight, dtype=precision, casting="unsafe") / total,
    )


def grouped_mean(values: ArrayLike, labels: ArrayLike, groups: int) -> FloatArray:
    """Dense group means in label order, with zeros for absent groups.

    *values* is N×D; *labels* contains N integers in ``[0, groups)``. *groups*
    is a nonnegative integer. Column-wise bincounts replace a full label scan
    and selected-row copy per group. Cost is O(ND + GD), output memory O(GD).
    Labels may be sparse within the declared range; no missing labels are dropped.
    """
    rows = _float(values, 2)
    index = _real(labels, 1)
    count = _count(groups, "groups")
    if index.size and index.dtype.kind not in "iu":
        msg = "labels must have an integer dtype"
        raise TypeError(msg)
    if index.size != rows.shape[0]:
        msg = "one label is required per matrix row"
        raise ValueError(msg)
    if index.size and (index.min() < 0 or index.max() >= count):
        msg = "labels must be in [0, groups)"
        raise ValueError(msg)
    positions = index.astype(np.intp, copy=False)
    frequencies = np.bincount(positions, minlength=count)
    out = np.empty((count, rows.shape[1]), dtype=np.float64)
    for column in range(rows.shape[1]):
        out[:, column] = np.bincount(
            positions, weights=rows[:, column], minlength=count
        )
    np.divide(out, frequencies[:, None], out=out, where=frequencies[:, None] != 0)
    return out


def frame(values: ArrayLike, length: int, hop: int) -> RealArray:
    """Read-only complete frames of a real vector, with no padding or frame copy.

    The result has shape ``(max(0, 1 + (N-length)//hop), length)``. Both sizes
    must be positive integers. Array inputs share storage, including strided
    inputs: later source edits remain visible. Use ``.copy()`` for independent
    mutable frames. List conversion allocates its source array once.
    """
    samples = _real(values, 1)
    width = _count(length, "length", 1)
    step = _count(hop, "hop", 1)
    if samples.size < width:
        out = np.empty((0, width), dtype=samples.dtype)
        out.flags.writeable = False
        return out
    return np.lib.stride_tricks.sliding_window_view(samples, width)[::step]
