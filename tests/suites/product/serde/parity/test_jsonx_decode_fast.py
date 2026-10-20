"""The native decoder preserves exact numeric values without a tree walk."""

from __future__ import annotations

import json
from typing import Any

import pytest

from unstd.serde import jsonx


pytestmark = pytest.mark.skipif(
    not jsonx._HAVE_MSGSPEC, reason="msgspec (`serde` extra) not installed"
)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize(
    "document",
    [
        "[0.0,-0.0,1e-4000,-1e-4000]",
        "[18446744073709551616,-9223372036854775809]",
        "[" + "9" * 640 + "]",
        "[1.00000000000000011102230246251565404236316680908203125]",
        "[1.00000000000000011102230246251565404236316680908203126]",
        '{"x":1.234567890123456789,"x":1}',
        '{"x":18446744073709551617,"x":1}',
        '{"é":1,"\\u00e9":2}',
        json.dumps({"brackets": "[{}]" * 5000, "values": list(range(1000))}),
    ],
)
def test_exact_numeric_and_wide_documents_avoid_stdlib_fallback(
    binary: bool, document: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = document.encode() if binary else document
    expected = json.loads(source)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("an exact native document fell back to stdlib")

    monkeypatch.setattr(jsonx._stdlib, "loads", forbidden)
    actual = jsonx.loads(source)
    # Re-encoding compares float bits, integer types, duplicate-key order and
    # nested values without NaN's unusual equality semantics.
    assert json.dumps(actual) == json.dumps(expected)


@pytest.mark.parametrize("container", [bytearray, memoryview])
def test_mutable_and_nonstdlib_inputs_bypass_native_decoder(
    container: type[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("native decoder inspected a nonexact source")

    monkeypatch.setattr(jsonx, "_decode", forbidden)
    source = container(b"{}")
    if container is bytearray:
        assert jsonx.loads(source) == {}
    else:
        with pytest.raises(TypeError):
            jsonx.loads(source)


@pytest.mark.parametrize("container", [str, bytes])
def test_source_type_checks_do_not_call_metaclass_equality(
    container: type[str] | type[bytes],
) -> None:
    class Equality(type):
        def __eq__(cls, other: object) -> bool:
            pytest.fail("native eligibility evaluated user-defined type equality")

    source_type = Equality("Document", (container,), {})
    source = source_type("{}" if container is str else b"{}")
    assert jsonx.loads(source) == {}
