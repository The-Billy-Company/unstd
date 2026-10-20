"""Atomic native proofs reject backend coercions before exposing any bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, IntEnum, StrEnum
import json
import math
import sys
from typing import Any
from uuid import UUID

import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
orjson = pytest.importorskip("orjson")


class _Enum(Enum):
    VALUE = "unsupported"


class _IntegerEnum(IntEnum):
    VALUE = 7


class _StringEnum(StrEnum):
    VALUE = "text"


@dataclass
class _Record:
    value: int = 7


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        -(2**63),
        2**64 - 1,
        "é😀\x7f\b\n",
        {"items": ("text", [1, 2, None]), "empty": {}},
        [{"text": "x" * 1000, "id": value} for value in range(40)],
    ],
)
@pytest.mark.parametrize("sort_keys", [False, True])
def test_native_compact_encoding_matches_stdlib(value: object, sort_keys: bool) -> None:
    expected = json.dumps(
        value, ensure_ascii=False, sort_keys=sort_keys, separators=(",", ":")
    ).encode()
    option = orjson.OPT_SORT_KEYS if sort_keys else 0
    assert native.encode(value, option) == expected


@pytest.mark.parametrize(
    "value",
    [
        0.0,
        -0.0,
        1e-5,
        math.inf,
        math.nan,
        _Enum.VALUE,
        _IntegerEnum.VALUE,
        _StringEnum.VALUE,
        _Record(),
        UUID(int=1),
        datetime(2026, 10, 19, tzinfo=UTC),
        b"bytes",
        {1: "integer key"},
        {None: "null key"},
        {"nested": [_Enum.VALUE]},
    ],
)
def test_backend_coercions_are_declined(value: object) -> None:
    assert native.encode(value) is NotImplemented


@pytest.mark.parametrize("value", [-(2**63) - 1, 2**64, 2**1000])
def test_backend_integer_range_errors_preserve_public_stdlib_output(
    value: int,
) -> None:
    with pytest.raises(orjson.JSONEncodeError):
        native.encode(value)
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    assert jsonx.dumpb(value) == expected


@pytest.mark.parametrize("kind", [str, int, float, list, tuple, dict])
def test_subclasses_are_declined_without_observing_their_methods(
    kind: type[Any],
) -> None:
    class Subclass(kind):
        def __iter__(self) -> Any:
            pytest.fail("native proof invoked subclass iteration")

        def __str__(self) -> str:
            pytest.fail("native proof invoked subclass string conversion")

        def __int__(self) -> int:
            pytest.fail("native proof invoked subclass integer conversion")

    value = Subclass({"x": 1}) if kind is dict else Subclass()
    assert native.encode([value]) is NotImplemented


def test_unknown_types_do_not_invoke_class_properties_or_metaclass_methods() -> None:
    class Meta(type):
        def __hash__(cls) -> int:
            pytest.fail("native proof hashed an unknown class")

        def __eq__(cls, other: object) -> bool:
            pytest.fail("native proof compared an unknown class")

    class Unknown(metaclass=Meta):
        @property
        def __class__(self) -> type:
            pytest.fail("native proof inspected the __class__ property")

    assert native.encode([Unknown()] * 8) is NotImplemented


@pytest.mark.parametrize("value", ["comma,", "colon:", {"key:": 1}, ["x,y"]])
def test_spacing_proof_rejects_string_punctuation(value: object) -> None:
    assert native.encode(value, 0, True) is NotImplemented
    assert native.encode(value, 0, False) == orjson.dumps(value)


def test_native_backend_options_support_indentation_and_newlines() -> None:
    value = {"items": [1, None], "empty": {}}
    assert (
        native.encode(value, orjson.OPT_INDENT_2, True)
        == json.dumps(value, ensure_ascii=False, indent=2).encode()
    )
    assert native.encode(value, orjson.OPT_APPEND_NEWLINE) == (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    )


def test_native_proof_stays_bounded_for_cycles_depth_and_total_values() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert native.encode(cyclic) is NotImplemented
    value: object = 0
    for _ in range(16):
        value = [value]
    assert native.encode(value) == orjson.dumps(value)
    assert native.encode([value]) is NotImplemented
    assert native.encode([None] * 65535) == orjson.dumps([None] * 65535)
    assert native.encode([None] * 65536) is NotImplemented


def test_trace_mutation_happens_before_native_eligibility() -> None:
    value = {"items": [1]}

    def invoke() -> object:
        return native.encode(value)

    def trace(frame: Any, event: str, argument: object) -> Any:
        if frame.f_code is invoke.__code__ and event == "line":
            value["items"].append(_Enum.VALUE)
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        assert invoke() is NotImplemented
    finally:
        sys.settrace(previous)


def test_profile_callbacks_cannot_run_between_proof_and_backend_call() -> None:
    value = {"items": [1]}
    events: list[str] = []
    backend_calls: list[str] = []

    def profile(frame: Any, event: str, argument: object) -> None:
        if argument is native.encode:
            events.append(event)
            if event == "c_call":
                value["items"].append(2)
            elif event == "c_return":
                value["items"].append(_Enum.VALUE)
        if argument is orjson.dumps:
            backend_calls.append(event)

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        encoded = native.encode(value)
    finally:
        sys.setprofile(previous)
    assert encoded == b'{"items":[1,2]}'
    assert value["items"] == [1, 2, _Enum.VALUE]
    assert events == ["c_call", "c_return"]
    assert backend_calls == []


def test_cached_backend_does_not_call_a_python_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replaced(*args: object, **kwargs: object) -> bytes:
        pytest.fail("native proof entered a Python backend callback")

    monkeypatch.setattr(orjson, "dumps", replaced)
    assert native.encode({"items": [1, 2]}) == b'{"items":[1,2]}'


def test_nonbuiltin_flags_are_not_evaluated() -> None:
    class Flag:
        def __bool__(self) -> bool:
            pytest.fail("native proof evaluated custom flag truthiness")

    assert native.encode({}, Flag()) is NotImplemented
    assert native.encode({}, 0, Flag()) is NotImplemented


def test_keyword_arguments_keep_the_same_native_proof() -> None:
    assert native.encode(obj={"value": 7}, spaced=True, option=0) == b'{"value":7}'
