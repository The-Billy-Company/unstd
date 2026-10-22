"""Differential encoding contracts against the running Python stdlib.

The reference is ``json.dumps``/``json.dump``, including their documented
extensions to JSON. Equal decoded values alone do not cover formatting,
unsupported types, callback behavior, or streaming failures. Sources:
https://docs.python.org/3.12/library/json.html and CPython's Lib/json.
"""

from __future__ import annotations

from collections import UserDict, deque, namedtuple
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from fractions import Fraction
import inspect
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import get_type_hints
from uuid import UUID

import pytest

from unstd.serde import jsonx


_HAVE_ORJSON = jsonx._HAVE_ORJSON


@pytest.fixture(params=["installed", "fallback"], autouse=True)
def _backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "installed" and not _HAVE_ORJSON:
        pytest.skip("orjson (`serde` extra) not installed")
    if request.param == "fallback":
        monkeypatch.setattr(jsonx, "_HAVE_ORJSON", False)


def _same_failure(value: object, **kwargs: object) -> None:
    with pytest.raises(Exception, match=r".") as expected:
        json.dumps(value, **kwargs)
    with pytest.raises(type(expected.value)) as actual:
        jsonx.dumps(value, **kwargs)
    assert type(actual.value) is type(expected.value)
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        [],
        {},
        (),
        {"z": [1, None, True], "a": {"text": "caf\u00e9 \U0001f600"}},
        '"\\\b\f\n\r\t\x00\x1f\x7f\u2028\u2029',
        pytest.param("\ud800", id="lone-high-surrogate"),
        pytest.param("\udfff", id="lone-low-surrogate"),
        pytest.param("\ud800\udfff", id="literal-surrogate-pair"),
    ],
)
@pytest.mark.parametrize("kwargs", [{}, {"ensure_ascii": False}])
def test_default_format_and_unicode(value: object, kwargs: dict[str, object]) -> None:
    assert jsonx.dumps(value, **kwargs) == json.dumps(value, **kwargs)


@pytest.mark.parametrize("indent", [None, 0, -1, 1, 2, 4, "", "\t", "--", False])
@pytest.mark.parametrize("separators", [None, (",", ":"), ("; ", " = ")])
def test_indent_and_separator_combinations(
    indent: int | str | None, separators: tuple[str, str] | None
) -> None:
    value = {"z": [1, {"x": "\u00e9"}], "a": {}, "b": []}
    kwargs = {"indent": indent, "separators": separators}
    assert jsonx.dumps(value, **kwargs) == json.dumps(value, **kwargs)


@pytest.mark.parametrize(
    "value",
    [
        -(2**100),
        -(2**63) - 1,
        -(2**63),
        2**53 + 1,
        2**64 - 1,
        2**64,
        2**100,
        0.0,
        -0.0,
        1e-5,
        1e-7,
        1e16,
        1e20,
        1e100,
        5e-324,
        sys.float_info.max,
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_number_text_is_identical(value: float) -> None:
    assert jsonx.dumps([value]) == json.dumps([value])
    assert jsonx.dumps([value], separators=(",", ":")) == json.dumps(
        [value], separators=(",", ":")
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_allow_nan_false_rejects_values_and_keys(value: float) -> None:
    _same_failure([value], allow_nan=False)
    _same_failure({value: "value"}, allow_nan=False)


class _Int(int):
    def __repr__(self) -> str:
        return "custom integer repr"


class _Float(float):
    def __repr__(self) -> str:
        return "custom float repr"


class _String(str):
    def __str__(self) -> str:
        return "custom string str"


class _IntegerEnum(IntEnum):
    VALUE = 7


class _StringEnum(StrEnum):
    VALUE = "\u00e9"


class _FloatEnum(float, Enum):
    VALUE = 1e-5


class _PlainEnum(Enum):
    VALUE = "secret"


class _List(list):
    pass


class _Dict(dict):
    pass


@pytest.mark.parametrize(
    "value",
    [
        _Int(12),
        _Float(1e-5),
        _String("\u00e9"),
        _IntegerEnum.VALUE,
        _StringEnum.VALUE,
        _FloatEnum.VALUE,
        _List([1, 2]),
        _Dict({"\u00e9": 1}),
        namedtuple("Point", "x y")(1, 2),
    ],
)
def test_supported_subclasses_do_not_call_default(value: object) -> None:
    def default(unexpected: object) -> object:
        pytest.fail(f"default called for supported value {type(unexpected)}")

    assert jsonx.dumps(value, default=default) == json.dumps(value, default=default)


@dataclass
class _Record:
    value: str = "must go through default"


@pytest.mark.parametrize(
    "value",
    [
        _Record(),
        _PlainEnum.VALUE,
        UUID("12345678-1234-5678-1234-567812345678"),
        datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        date(2026, 1, 2),
        time(3, 4),
        Decimal("1.25"),
        Fraction(1, 3),
        1 + 2j,
        b"bytes",
        bytearray(b"bytes"),
        memoryview(b"bytes"),
        {1, 2},
        frozenset({1, 2}),
        range(3),
        deque([1, 2]),
        UserDict({"key": 1}),
        MappingProxyType({"key": 1}),
        Path("file.json"),
    ],
)
def test_unsupported_types_require_default(value: object) -> None:
    _same_failure(value)
    calls: list[object] = []

    def default(obj: object) -> object:
        calls.append(obj)
        return {"custom": type(obj).__name__}

    expected = json.dumps([value], default=default)
    assert len(calls) == 1
    assert calls[0] is value
    calls.clear()
    assert jsonx.dumps([value], default=default) == expected
    assert len(calls) == 1
    assert calls[0] is value


@pytest.mark.parametrize(
    "value",
    [
        {None: "none", True: "true", False: "false", 2: "int", 2.5: "float"},
        {float("nan"): "nan", float("inf"): "inf", -float("inf"): "negative"},
        {2**100: "large", -(2**100): "negative"},
        {_Int(3): "integer", _Float(1e-5): "float", _String("\u00e9"): "str"},
        {1: "int", "1": "str", None: "none", "null": "str"},
        {"z": 0, "a": 1, "m": 2},
    ],
)
def test_dictionary_key_coercion_and_insertion_order(
    value: dict[object, object],
) -> None:
    assert jsonx.dumps(value) == json.dumps(value)


@pytest.mark.parametrize(
    "value", [{10: "ten", 2: "two", -3: "negative"}, {"z": 0, "\u00e9": 1, "a": 2}]
)
def test_sort_keys_uses_original_keys(value: dict[object, object]) -> None:
    assert jsonx.dumps(value, sort_keys=True) == json.dumps(value, sort_keys=True)


@pytest.mark.parametrize("skipkeys", [False, True])
def test_mixed_keys_sort_error_precedes_key_coercion(skipkeys: bool) -> None:
    _same_failure({1: "int", "1": "str"}, sort_keys=True, skipkeys=skipkeys)


@pytest.mark.parametrize(
    "key", [(1, 2), _PlainEnum.VALUE, UUID(int=1), date(2026, 1, 2)]
)
def test_invalid_keys_are_not_passed_to_default(key: object) -> None:
    calls: list[object] = []

    def default(obj: object) -> object:
        calls.append(obj)
        return "converted"

    value = {key: "invalid", "keep": 1}
    _same_failure(value, default=default)
    assert calls == []
    assert jsonx.dumps(value, skipkeys=True, default=default) == json.dumps(
        value, skipkeys=True, default=default
    )
    assert calls == []


def test_default_can_return_nested_values_and_chain_to_another_unknown_type() -> None:
    first, second = object(), object()

    def encode(dumps: object) -> tuple[str, list[object]]:
        calls: list[object] = []

        def default(obj: object) -> object:
            calls.append(obj)
            return {"next": second} if obj is first else ["\u00e9", 2**100, 1e-5]

        return dumps(first, default=default), calls

    expected, expected_calls = encode(json.dumps)
    actual, actual_calls = encode(jsonx.dumps)
    assert actual == expected
    assert actual_calls == expected_calls == [first, second]


def test_default_exception_is_not_wrapped_or_retried() -> None:
    failure = LookupError("caller exception")
    value = object()
    calls: list[object] = []
    for dumps in (json.dumps, jsonx.dumps):
        calls.clear()

        def default(obj: object) -> object:
            calls.append(obj)
            raise failure

        with pytest.raises(LookupError) as exc:
            dumps(value, default=default)
        assert exc.value is failure
        assert calls == [value]


@pytest.mark.parametrize("kind", ["list", "dict", "default"])
def test_circular_references_raise_value_error(kind: str) -> None:
    if kind == "list":
        value: object = []
        value.append(value)
    elif kind == "dict":
        value = {}
        value["self"] = value
    else:
        value = object()
    _same_failure(value, default=lambda obj: obj)


def test_reused_container_is_not_mistaken_for_cycle() -> None:
    shared = {"value": [1, 2]}
    value = [shared, shared]
    assert jsonx.dumps(value) == json.dumps(value)


def test_disabling_cycle_check_preserves_recursion_error() -> None:
    value: list[object] = []
    value.append(value)
    with pytest.raises(RecursionError):
        json.dumps(value, check_circular=False)
    with pytest.raises(RecursionError):
        jsonx.dumps(value, check_circular=False)


def test_nesting_beyond_orjson_encoding_limit() -> None:
    value: object = 0
    for _ in range(300):
        value = [value]
    assert jsonx.dumps(value) == json.dumps(value)


def test_integer_string_conversion_limit_is_preserved() -> None:
    limit = sys.get_int_max_str_digits()
    if limit == 0:
        pytest.skip("interpreter integer string conversion limit disabled")
    _same_failure(10**limit)


def test_custom_encoder_receives_all_keywords_and_default_values() -> None:
    observed: list[dict[str, object]] = []

    class Encoder(json.JSONEncoder):
        def __init__(self, *, custom: str, **kwargs: object) -> None:
            observed.append({"custom": custom, **kwargs})
            super().__init__(**kwargs)

        def default(self, obj: object) -> object:
            return "custom encoder"

    expected = json.dumps(object(), cls=Encoder, custom="constructor option")
    actual = jsonx.dumps(object(), cls=Encoder, custom="constructor option")
    assert actual == expected
    assert observed[0] == observed[1]


@pytest.mark.parametrize("api_name", ["dumps", "dump"])
def test_standard_encoder_signatures_match_names_kinds_and_defaults(
    api_name: str,
) -> None:
    def parameters(api: object) -> list[tuple[str, object, object]]:
        return [
            (parameter.name, parameter.kind, parameter.default)
            for parameter in inspect.signature(api).parameters.values()
        ]

    assert parameters(getattr(jsonx, api_name)) == parameters(getattr(json, api_name))


@pytest.mark.parametrize("numpy_option", [True, False, None, [], object()])
@pytest.mark.parametrize("api_name", ["dumps", "dump"])
def test_custom_encoder_receives_its_own_numpy_keyword(
    api_name: str, numpy_option: object
) -> None:
    observed: list[object] = []

    class Encoder(json.JSONEncoder):
        def __init__(self, *, numpy: object, **kwargs: object) -> None:
            observed.append(numpy)
            super().__init__(**kwargs)

        def default(self, obj: object) -> object:
            return "custom encoder"

    kwargs = {"cls": Encoder, "numpy": numpy_option}
    if api_name == "dumps":
        expected = json.dumps(object(), **kwargs)
        actual = jsonx.dumps(object(), **kwargs)
    else:
        expected_fp, actual_fp = StringIO(), StringIO()
        json.dump(object(), expected_fp, **kwargs)
        jsonx.dump(object(), actual_fp, **kwargs)
        expected, actual = expected_fp.getvalue(), actual_fp.getvalue()
    assert actual == expected
    assert len(observed) == 2
    assert observed[0] is numpy_option
    assert observed[1] is numpy_option


@pytest.mark.parametrize("api_name", ["dumps", "dump"])
def test_explicit_standard_encoder_rejects_numpy_keyword(api_name: str) -> None:
    kwargs = {"cls": json.JSONEncoder, "numpy": True}
    args: tuple[object, ...] = ({}, StringIO()) if api_name == "dump" else ({},)
    with pytest.raises(TypeError) as expected:
        getattr(json, api_name)(*args, **kwargs)
    with pytest.raises(TypeError) as actual:
        getattr(jsonx, api_name)(*args, **kwargs)
    assert str(actual.value) == str(expected.value)


def test_explicit_default_overrides_encoder_default() -> None:
    class Encoder(json.JSONEncoder):
        def default(self, obj: object) -> object:
            return "encoder default"

    kwargs = {"cls": Encoder, "default": lambda obj: "explicit default"}
    assert jsonx.dumps(object(), **kwargs) == json.dumps(object(), **kwargs)


def test_unrecognized_keyword_raises_the_standard_constructor_error() -> None:
    _same_failure({}, unsupported_option=True)


class _Writer:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, chunk: str) -> None:
        assert isinstance(chunk, str)
        self.chunks.append(chunk)


@pytest.mark.parametrize("kwargs", [{}, {"indent": 2}, {"ensure_ascii": False}])
def test_dump_writes_text_and_returns_none(kwargs: dict[str, object]) -> None:
    value = {"text": "\u00e9", "list": [1, 2]}
    expected, actual = StringIO(), StringIO()
    assert json.dump(value, expected, **kwargs) is None
    assert jsonx.dump(value, actual, **kwargs) is None
    assert actual.getvalue() == expected.getvalue()


def test_dump_uses_custom_iterencode_instead_of_encode() -> None:
    class Encoder(json.JSONEncoder):
        def encode(self, obj: object) -> str:
            pytest.fail("dump should use iterencode")

        def iterencode(self, obj: object, _one_shot: bool = False) -> object:
            yield "first"
            yield "second"

    expected, actual = _Writer(), _Writer()
    json.dump(object(), expected, cls=Encoder)
    jsonx.dump(object(), actual, cls=Encoder)
    assert actual.chunks == expected.chunks == ["first", "second"]


def test_dump_preserves_partial_output_on_serialization_failure() -> None:
    value = [1, {"unsupported": object()}]
    expected, actual = _Writer(), _Writer()
    with pytest.raises(TypeError):
        json.dump(value, expected)
    with pytest.raises(TypeError):
        jsonx.dump(value, actual)
    assert "".join(actual.chunks) == "".join(expected.chunks)
    assert expected.chunks


def test_dump_propagates_write_failure_before_encoding_later_values() -> None:
    failure = OSError("write failed")

    class BrokenWriter:
        def write(self, chunk: str) -> None:
            raise failure

    calls: list[object] = []
    for dump in (json.dump, jsonx.dump):
        calls.clear()

        def default(obj: object) -> object:
            calls.append(obj)
            return "converted"

        with pytest.raises(OSError, match="write failed") as exc:
            dump([object()], BrokenWriter(), default=default)
        assert exc.value is failure
        assert calls == []


def test_dump_to_binary_file_rejects_str() -> None:
    with pytest.raises(TypeError) as expected:
        json.dump({}, BytesIO())
    with pytest.raises(TypeError) as actual:
        jsonx.dump({}, BytesIO())
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize(
    "value",
    [
        {"text": "\u00e9\U0001f600", "items": [1, True, None]},
        [2**100, -(2**100)],
        [float("nan"), float("inf"), -float("inf")],
        {1: "int", "1": "str", None: "null", float("inf"): "infinite"},
        [_Float(1e-5), _IntegerEnum.VALUE, _StringEnum.VALUE],
        namedtuple("Coordinate", "x y")(1, 2),
    ],
)
@pytest.mark.parametrize("sort_keys", [False, True])
def test_dumpb_matches_compact_utf8_stdlib(value: object, sort_keys: bool) -> None:
    kwargs = {"sort_keys": sort_keys, "ensure_ascii": False, "separators": (",", ":")}
    error: TypeError | None = None
    try:
        expected = json.dumps(value, **kwargs).encode()
    except TypeError as expected_error:
        error = expected_error
    if error is not None:
        with pytest.raises(TypeError) as actual:
            jsonx.dumpb(value, sort_keys=sort_keys)
        assert str(actual.value) == str(error)
    else:
        assert jsonx.dumpb(value, sort_keys=sort_keys) == expected


def test_dumpb_preserves_finite_float_values_types_and_zero_sign() -> None:
    values = [-0.0, 0.0, 1.0, 1e-5, 1e-7, 1e20, 5e-324, sys.float_info.max]
    actual = json.loads(jsonx.dumpb(values))
    assert actual == values
    assert all(type(value) is float for value in actual)
    assert [math.copysign(1, value) for value in actual] == [
        math.copysign(1, value) for value in values
    ]


def test_dumpb_does_not_probe_custom_class_property_before_stdlib() -> None:
    class Proxy:
        def __init__(self) -> None:
            self.lookups: list[str] = []

        @property
        def __class__(self) -> type:
            self.lookups.append("class lookup")
            return int

    expected, actual = Proxy(), Proxy()
    with pytest.raises(TypeError) as expected_error:
        json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    with pytest.raises(TypeError) as actual_error:
        jsonx.dumpb(actual)
    assert str(actual_error.value) == str(expected_error.value)
    assert actual.lookups == expected.lookups


@pytest.mark.parametrize("value", [_Record(), _PlainEnum.VALUE, UUID(int=1)])
def test_dumpb_passes_unsupported_values_to_default(value: object) -> None:
    _same_failure(value)
    with pytest.raises(TypeError):
        jsonx.dumpb(value)
    calls: list[object] = []

    def default(obj: object) -> object:
        calls.append(obj)
        return {"custom": "\u00e9"}

    expected = json.dumps(
        value, default=default, ensure_ascii=False, separators=(",", ":")
    ).encode()
    calls.clear()
    assert jsonx.dumpb(value, default=default) == expected
    assert len(calls) == 1
    assert calls[0] is value


def test_dumpb_and_line_preserve_deep_nesting() -> None:
    value: object = 1e-5
    for _ in range(300):
        value = [value]
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    assert jsonx.dumpb(value) == expected
    assert jsonx._line(value) == expected + b"\n"


def test_dumpb_eligibility_walk_respects_a_low_python_recursion_limit() -> None:
    value: object = 0
    for _ in range(60):
        value = [value]
    limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(120)
        expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        assert jsonx.dumpb(value) == expected
    finally:
        sys.setrecursionlimit(limit)


def test_dumpb_rejects_lone_surrogates_like_utf8_encoding_stdlib_output() -> None:
    value = "\ud800"
    with pytest.raises(UnicodeEncodeError):
        json.dumps(value, ensure_ascii=False).encode()
    with pytest.raises(UnicodeEncodeError):
        jsonx.dumpb(value)


def test_canonical_uses_ascii_sorted_compact_stdlib() -> None:
    value = {"z": [2**100, float("nan"), 1e-5], "\u00e9": "\ud800"}
    expected = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str
    ).encode()
    assert jsonx.canonical(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        2**100,
        -0.0,
        1.25,
        "café 😀",
        [],
        {},
        {"z": [True, None, 1, 1.0, -0.0], "a": {"text": "é"}},
    ],
)
def test_strict_identity_matches_the_defined_legacy_format(value: object) -> None:
    expected = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    assert jsonx.canonical_strict(value) == expected
    assert jsonx.canonical_strict(value) == jsonx.canonical(value)


def test_strict_identity_has_frozen_unicode_and_numeric_spellings() -> None:
    assert jsonx.canonical_strict({"é": "😀", "a": [True, None, 1, 1.0, -0.0]}) == (
        b'{"a":[true,null,1,1.0,-0.0],"\\u00e9":"\\ud83d\\ude00"}'
    )
    assert jsonx.canonical_strict("é") != jsonx.canonical_strict("e\u0301")


@pytest.mark.parametrize(
    "value",
    [b"a", bytearray(b"a"), Decimal("1.0"), {"a", "b"}, (1, 2), object(), {1: "value"}],
)
def test_strict_identity_refuses_implicit_coercions(value: object) -> None:
    with pytest.raises(TypeError):
        jsonx.canonical_strict(value)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "\ud800",
        {"\udfff": 1},
        "\ud83d\ude00",
        {"\ud83d\ude00": 1},
    ],
)
def test_strict_identity_refuses_nonfinite_numbers_and_surrogate_codepoints(
    value: object,
) -> None:
    with pytest.raises(ValueError, match=r"finite|surrogate"):
        jsonx.canonical_strict(value)


def test_strict_identity_rejects_cycles_but_accepts_shared_branches() -> None:
    branch = [1, {"key": "value"}]
    assert (
        jsonx.canonical_strict([branch, branch])
        == b'[[1,{"key":"value"}],[1,{"key":"value"}]]'
    )
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="circular"):
        jsonx.canonical_strict(cycle)
    mapping: dict[str, object] = {}
    mapping["self"] = mapping
    with pytest.raises(ValueError, match="circular"):
        jsonx.canonical_strict(mapping)


def test_strict_identity_rejects_subclasses_without_running_their_hooks() -> None:
    class Text(str):
        def __str__(self):
            pytest.fail("identity serialization invoked a conversion hook")

    class Values(list):
        def __iter__(self):
            pytest.fail("identity serialization invoked an iteration hook")

    for value in (Text("value"), Values([1]), {Text("key"): 1}):
        with pytest.raises(TypeError):
            jsonx.canonical_strict(value)


def test_strict_identity_owns_the_validated_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {"values": [1]}
    encoder = jsonx._IDENTITY.encode

    def observed(snapshot):
        source["values"].append(2)
        return encoder(snapshot)

    monkeypatch.setattr(jsonx._IDENTITY, "encode", observed)
    assert jsonx.canonical_strict(source) == b'{"values":[1]}'
    assert source == {"values": [1, 2]}


def test_legacy_identity_coercions_remain_compatible() -> None:
    assert jsonx.canonical(b"a") == jsonx.canonical("b'a'")
    assert jsonx.canonical(Decimal("1.0")) == b'"1.0"'
    assert jsonx.canonical(float("nan")) == b"NaN"


def test_strict_identity_is_independent_of_process_hash_seed() -> None:
    script = (
        "from unstd.serde import jsonx; import sys; "
        "value = {key: {'b': [1, 2], 'a': 'é'} for key in {'z', 'a', 'm'}}; "
        "sys.stdout.buffer.write(jsonx.canonical_strict(value))"
    )
    outputs = [
        subprocess.run(  # noqa: S603 — fixed source and the current interpreter
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": seed},
            timeout=10,
            check=True,
            capture_output=True,
        ).stdout
        for seed in ("1", "2", "173")
    ]
    assert outputs == [outputs[0]] * len(outputs)


def test_public_json_annotations_resolve_for_runtime_consumers() -> None:
    for function in (jsonx.dumps, jsonx.dumpb, jsonx.dump, jsonx.load, jsonx.canonical):
        get_type_hints(function)
    assert get_type_hints(jsonx.canonical_strict) == {"obj": object, "return": bytes}
