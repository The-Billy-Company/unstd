"""Native indented proofs keep exact output while admitting wide documents."""

from __future__ import annotations

import json
import math
import sys
from types import SimpleNamespace
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx
from unstd.serde._internal import json_encode


native = pytest.importorskip("unstd.serde._json_native")
orjson = pytest.importorskip("orjson")


@pytest.mark.parametrize("sort_keys", [False, True])
def test_indented_native_proof_does_not_limit_wide_documents(sort_keys: bool) -> None:
    value = [{"z": index, "a": [None, True, "text"]} for index in range(20_000)]
    option = orjson.OPT_INDENT_2 | (orjson.OPT_SORT_KEYS if sort_keys else 0)
    expected = json.dumps(value, indent=2, sort_keys=sort_keys).encode()
    assert native.encode_indent(value, option) == expected


@pytest.mark.parametrize("options", [{}, {"separators": (",", ":")}, {"indent": 2}])
def test_public_wide_documents_avoid_the_stdlib_fallback(
    options: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    value = list(range(100_000))
    expected = json.dumps(value, **options)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("a wide builtin tree fell back to stdlib encoding")

    monkeypatch.setattr(jsonx, "_DEFAULT_ENCODER", SimpleNamespace(encode=forbidden))
    monkeypatch.setattr(json_encode, "_encoder", forbidden)
    assert jsonx.dumps(value, **options) == expected


@pytest.mark.parametrize("size", [65_535, 65_536, 100_000])
def test_indented_proof_accepts_each_value_beyond_the_compact_proof_budget(
    size: int,
) -> None:
    value = [None] * size
    assert (
        native.encode_indent(value, orjson.OPT_INDENT_2)
        == json.dumps(value, indent=2).encode()
    )


@pytest.mark.parametrize(
    "unsupported", [math.nan, math.inf, -math.inf, object(), {1: 2}]
)
def test_indented_proof_rejects_an_unsupported_value_at_the_end(
    unsupported: object,
) -> None:
    value = [None] * 100_000 + [unsupported]
    assert native.encode_indent(value, orjson.OPT_INDENT_2) is NotImplemented


def test_indented_proof_retains_depth_and_cycle_boundaries() -> None:
    value: object = 0
    for _ in range(16):
        value = [value]
    assert (
        native.encode_indent(value, orjson.OPT_INDENT_2)
        == json.dumps(value, indent=2).encode()
    )
    assert native.encode_indent([value], orjson.OPT_INDENT_2) is NotImplemented
    cycle: list[object] = []
    cycle.append(cycle)
    assert native.encode_indent(cycle, orjson.OPT_INDENT_2) is NotImplemented


def test_indented_proof_does_not_evaluate_option_subclass_methods() -> None:
    class Option(int):
        def __int__(self) -> int:
            pytest.fail("the proof converted an option subclass")

    assert native.encode_indent([1], Option(orjson.OPT_INDENT_2)) is NotImplemented


@pytest.mark.skipif(
    sys.version_info >= (3, 13),
    reason="newer stdlib already encodes indented floats in C",
)
@pytest.mark.parametrize("sort_keys", [False, True])
@given(values=st.lists(st.floats(allow_nan=False, allow_infinity=False), max_size=100))
def test_indented_float_tokens_use_the_stdlib_number_spelling(
    values: list[float], sort_keys: bool
) -> None:
    value = {"values": values, "comma:,": "1e-5, 1e+20: 0.00001"}
    option = orjson.OPT_INDENT_2 | (orjson.OPT_SORT_KEYS if sort_keys else 0)
    assert (
        native.encode_indent(value, option)
        == json.dumps(value, indent=2, sort_keys=sort_keys).encode()
    )


@pytest.mark.skipif(
    sys.version_info >= (3, 13),
    reason="newer stdlib already encodes indented floats in C",
)
@pytest.mark.parametrize(
    "value",
    [0.0, -0.0, 1e-5, 1e-6, 1e16, 1e20, 5e-324, -5e-324, 1.7976931348623157e308],
)
def test_indented_float_extremes_and_notation_boundaries(value: float) -> None:
    assert (
        native.encode_indent([value], orjson.OPT_INDENT_2)
        == json.dumps([value], indent=2).encode()
    )


@pytest.mark.skipif(sys.version_info < (3, 13), reason="Python indented encoder")
def test_newer_python_keeps_the_native_stdlib_float_encoder() -> None:
    assert native.encode_indent([1.25], orjson.OPT_INDENT_2) is NotImplemented
