"""The explicit numpy extension has the same conversions on every backend."""

from __future__ import annotations

from io import StringIO
import json

import pytest

from unstd.serde import jsonx


try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # numpy is not part of the `serde` extra
    _HAVE_NUMPY = False

requires_numpy = pytest.mark.skipif(not _HAVE_NUMPY, reason="numpy not installed")


@pytest.fixture(params=[True, False], autouse=True)
def _backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param and not jsonx._HAVE_ORJSON:
        pytest.skip("orjson (`serde` extra) not installed")
    monkeypatch.setattr(jsonx, "_HAVE_ORJSON", request.param)
    monkeypatch.setattr(jsonx, "_HAVE_MSGSPEC", request.param and jsonx._HAVE_MSGSPEC)


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
    expected = json.dumps(arr.tolist(), ensure_ascii=False, separators=(",", ":"))
    assert jsonx.dumpb(arr, numpy=True) == expected.encode()


@requires_numpy
def test_numpy_true_nested_in_dict() -> None:
    payload = {"weights": np.array([0.1, 0.2]), "n": np.int32(2)}
    decoded = jsonx.loads(jsonx.dumps(payload, numpy=True))
    assert decoded == {"weights": [0.1, 0.2], "n": 2}


@requires_numpy
def test_numpy_true_respects_format_kwargs() -> None:
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


@requires_numpy
def test_numpy_float32_uses_full_python_float_precision() -> None:
    arr = np.array([0.1, 1e-5], dtype=np.float32)
    assert jsonx.dumps(arr, numpy=True) == json.dumps(arr.tolist())


@requires_numpy
def test_numpy_values_keep_stdlib_numeric_semantics() -> None:
    arr = np.array([float("nan"), float("inf"), -float("inf")])
    assert jsonx.dumps(arr, numpy=True) == json.dumps(arr.tolist())
    with pytest.raises(ValueError, match="Out of range float"):
        jsonx.dumps(arr, numpy=True, allow_nan=False)
    large = np.array([2**100], dtype=object)
    assert jsonx.dumps(large, numpy=True) == json.dumps(large.tolist())


@requires_numpy
def test_numpy_dump_writes_stdlib_formatted_text() -> None:
    arr = np.array([1, 2])
    fp = StringIO()
    assert jsonx.dump(arr, fp, numpy=True, indent="\t") is None
    assert fp.getvalue() == json.dumps(arr.tolist(), indent="\t")


def test_numpy_true_without_numpy_installed_raises_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jsonx, "_HAVE_NUMPY", False)
    with pytest.raises(TypeError, match="numpy"):
        jsonx.dumps([1, 2, 3], numpy=True)
    with pytest.raises(TypeError, match="numpy"):
        jsonx.dumpb([1, 2, 3], numpy=True)
