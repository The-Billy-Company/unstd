"""Unit tests for ``unstd.serde.jsonx`` beyond the parity property suite.

``tests/properties/test_serde_parity.py`` proves the shared JSON-grammar contract
against stdlib ``json``. This file pins the two things a property test can't:
the deliberate value divergences from stdlib (documented, not covered by the
parity domain) and the ``numpy=`` passthrough, on both the orjson fast path and
the stdlib fallback path.
"""

from __future__ import annotations

import math

import pytest

from unstd.serde import jsonx


try:
    import orjson  # noqa: F401

    _HAVE_ORJSON = True
except ImportError:  # base install (no `serde` extra) — orjson-only cases skipped
    _HAVE_ORJSON = False

try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # numpy is not part of the `serde` extra
    _HAVE_NUMPY = False

requires_orjson = pytest.mark.skipif(
    not _HAVE_ORJSON, reason="orjson (`serde` extra) not installed"
)
requires_numpy = pytest.mark.skipif(not _HAVE_NUMPY, reason="numpy not installed")


# ── documented value divergences (orjson backend only — no fallback recovers these) ──


@requires_orjson
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_floats_serialize_to_null(value: float) -> None:
    assert jsonx.dumps(value) == "null"


@requires_orjson
def test_int_beyond_64_bit_raises_on_encode() -> None:
    with pytest.raises(TypeError):
        jsonx.dumps(2**64)


@requires_orjson
def test_out_of_range_int_literal_decodes_lossy_as_float() -> None:
    decoded = jsonx.loads(str(2**64))
    assert isinstance(decoded, float)


# ── numpy=True passthrough ──────────────────────────────────────────────────


@requires_numpy
def test_numpy_false_raises_on_ndarray_without_default() -> None:
    with pytest.raises(TypeError):
        jsonx.dumps(np.array([1, 2, 3]))


@requires_numpy
def test_numpy_true_serializes_ndarray() -> None:
    arr = np.array([1, 2, 3], dtype=np.int64)
    assert jsonx.loads(jsonx.dumps(arr, numpy=True)) == [1, 2, 3]


@requires_numpy
def test_numpy_true_serializes_scalar() -> None:
    assert jsonx.loads(jsonx.dumps(np.int64(7), numpy=True)) == 7
    assert jsonx.loads(jsonx.dumps(np.float64(1.5), numpy=True)) == 1.5


@requires_numpy
def test_numpy_true_dumpb_matches_dumps() -> None:
    arr = np.array([1.0, 2.0, 3.0])
    assert jsonx.dumpb(arr, numpy=True) == jsonx.dumps(arr, numpy=True).encode()


@requires_numpy
def test_numpy_true_nested_in_dict() -> None:
    payload = {"weights": np.array([0.1, 0.2]), "n": np.int32(2)}
    decoded = jsonx.loads(jsonx.dumps(payload, numpy=True))
    assert decoded == {"weights": [0.1, 0.2], "n": 2}


@requires_numpy
def test_numpy_true_forces_stdlib_path_when_format_kwarg_present() -> None:
    """``ensure_ascii=True`` forces the stdlib fallback branch even with orjson
    installed — the numpy value must still degrade via the wrapped ``default``.
    """
    out = jsonx.dumps(np.array([1, 2]), numpy=True, ensure_ascii=True)
    assert jsonx.loads(out) == [1, 2]


@requires_numpy
def test_numpy_true_composes_with_user_default() -> None:
    class Sentinel:
        pass

    def default(o: object) -> object:
        if isinstance(o, Sentinel):
            return "sentinel"
        raise TypeError

    payload = {"arr": np.array([1]), "s": Sentinel()}
    decoded = jsonx.loads(
        jsonx.dumps(payload, numpy=True, default=default, ensure_ascii=True)
    )
    assert decoded == {"arr": [1], "s": "sentinel"}


def test_numpy_true_without_numpy_installed_raises_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jsonx, "_HAVE_NUMPY", False)
    with pytest.raises(TypeError, match="numpy"):
        jsonx.dumps([1, 2, 3], numpy=True)
    with pytest.raises(TypeError, match="numpy"):
        jsonx.dumpb([1, 2, 3], numpy=True)
