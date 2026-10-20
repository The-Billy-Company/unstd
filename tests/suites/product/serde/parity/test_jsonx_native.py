"""Atomic native proofs reject backend coercions before exposing any bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, IntEnum, StrEnum
import inspect
import json
import math
import subprocess
import sys
import textwrap
from types import FunctionType
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


@pytest.mark.parametrize(
    "value",
    [
        "comma,",
        "colon:",
        {"key:": 1},
        ["x,y"],
        {"punctuation,:": ['quoted "text",: and trailing \\', {"empty": []}]},
        {"é,:": ["😀\x00\x1f\x7f,:", (), {}, True, None, -(2**63), 2**64 - 1]},
    ],
)
@pytest.mark.parametrize("sort_keys", [False, True])
def test_native_default_spacing_preserves_string_content(
    value: object, sort_keys: bool
) -> None:
    option = orjson.OPT_SORT_KEYS if sort_keys else 0
    expected = json.dumps(value, ensure_ascii=False, sort_keys=sort_keys).encode()
    assert native.encode(value, option, True) == expected
    assert native.encode(value, option, False) == orjson.dumps(value, option=option)
    assert (
        jsonx.dumps(value, ensure_ascii=False, sort_keys=sort_keys).encode() == expected
    )
    assert jsonx.dumps(value, sort_keys=sort_keys) == json.dumps(
        value, sort_keys=sort_keys
    )


@pytest.mark.parametrize("backslashes", range(12))
def test_native_spacing_handles_escaped_quotes_and_trailing_backslashes(
    backslashes: int,
) -> None:
    text = "\\" * backslashes
    value = {text + '",:': [text, '"' + text, {"tail": text + '"'}]}
    assert native.encode(value, 0, True) == json.dumps(value).encode()


def test_public_default_spacing_accepts_a_larger_punctuated_text_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unstd.serde import _json_encode

    value = {"records:": [{"text,": 'quoted "words", path ' + "\\" + " : " * 20}] * 40}
    expected = json.dumps(value)
    observed: list[bytes] = []

    def encode(obj: object, option: int, spaced: bool) -> Any:
        assert spaced
        result = native.encode(obj, option, spaced)
        assert result is not NotImplemented
        observed.append(result)
        return result

    monkeypatch.setattr(_json_encode, "_native_encode", encode)
    assert jsonx.dumps(value) == expected
    assert observed == [expected.encode()]


def test_native_backend_options_support_indentation_and_newlines() -> None:
    value = {"items": [1, None], "empty": {}}
    assert (
        native.encode(value, orjson.OPT_INDENT_2, False)
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
    assert native.encode(obj={"value": 7}, spaced=True, option=0) == b'{"value": 7}'


@pytest.fixture
def configured_native() -> Any:
    native.configure(jsonx.__dict__)
    return native


def test_transport_helpers_share_the_proof_and_native_options(
    configured_native: Any,
) -> None:
    value = {"z": (1, "é"), "a": {"value": None}}
    for sort_keys in (False, True):
        expected = json.dumps(
            value,
            sort_keys=sort_keys,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        assert configured_native.dumpb(value, sort_keys, None, False) == expected
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    assert configured_native.line(value) == expected + b"\n"


def test_transport_helpers_decline_custom_behavior_without_callbacks(
    configured_native: Any,
) -> None:
    class Flag:
        def __bool__(self) -> bool:
            pytest.fail("native transport evaluated caller truthiness")

    def default(value: object) -> object:
        pytest.fail("native transport invoked caller default")

    assert configured_native.dumpb({}, Flag(), None, False) is NotImplemented
    assert configured_native.dumpb({}, False, default, False) is NotImplemented
    assert configured_native.dumpb({}, False, None, Flag()) is NotImplemented
    assert configured_native.dumpb({}, False, None, True) is NotImplemented
    assert configured_native.dumpb([math.nan], False, None, False) is NotImplemented
    assert configured_native.line(_Record()) is NotImplemented


@pytest.mark.parametrize("flag", ["backend", "availability"])
def test_transport_helpers_observe_backend_monkeypatches(
    configured_native: Any, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    if flag == "backend":
        monkeypatch.setattr(jsonx, "_encode", lambda value: b"replacement")
    else:
        monkeypatch.setattr(jsonx, "_HAVE_ORJSON", False)
    assert configured_native.dumpb({}, False, None, False) is NotImplemented
    assert configured_native.line({}) is NotImplemented


def test_transport_helpers_return_backend_failures_to_the_python_fallback(
    configured_native: Any,
) -> None:
    for value in (2**1000, "\ud800"):
        assert configured_native.dumpb(value, False, None, False) is NotImplemented
        assert configured_native.line(value) is NotImplemented


def test_transport_fast_paths_preserve_exhausted_python_stack_failures() -> None:
    source = textwrap.dedent(
        """
        from functools import partial
        import json
        import sys
        from unstd.serde import jsonx

        def invoke(function, value, depth):
            if depth:
                return invoke(function, value, depth - 1)
            return function(value)

        def accepted(function, value, depth):
            try:
                invoke(function, value, depth)
            except RecursionError:
                return False
            return True

        reference = partial(json.dumps, ensure_ascii=False, separators=(",", ":"))
        violations = []
        for limit in (120, 180):
            sys.setrecursionlimit(limit)
            for nesting in (1, 16, 64):
                value = json.loads("[" * nesting + "0" + "]" * nesting)
                for name, actual in (("dumpb", jsonx.dumpb), ("line", jsonx._line)):
                    for depth in range(limit - 14, limit):
                        expected = accepted(reference, value, depth)
                        observed = accepted(actual, value, depth)
                        if observed and not expected:
                            violations.append((name, limit, nesting, depth))
        assert not violations, violations
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed isolated recursion probe
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def bound_encoder(configured_native: Any) -> Any:
    if sys.version_info[:2] != (3, 14):
        pytest.skip("Python function dispatch is specialized for CPython 3.14")
    function = jsonx.dumpb
    assert configured_native.is_bound(function)
    yield function
    configured_native.configure(jsonx.__dict__)


def test_bound_encoder_keeps_the_actual_python_function_metadata(
    bound_encoder: Any,
) -> None:
    function = bound_encoder
    clone = FunctionType(function.__code__, function.__globals__, function.__name__)
    clone.__kwdefaults__ = function.__kwdefaults__
    clone.__annotations__ = function.__annotations__
    assert isinstance(function, FunctionType)
    assert inspect.signature(function) == inspect.signature(clone)
    assert function.__code__ is clone.__code__
    assert function.__annotations__ is clone.__annotations__
    assert function(obj={"z": 0, "a": 1}, sort_keys=True) == b'{"a":1,"z":0}'


def test_bound_encoder_observes_in_place_keyword_default_changes(
    bound_encoder: Any,
) -> None:
    defaults = bound_encoder.__kwdefaults__
    original = dict(defaults)
    try:
        defaults["sort_keys"] = True
        assert bound_encoder({"z": 0, "a": 1}) == b'{"a":1,"z":0}'
        defaults["sort_keys"] = False
        assert bound_encoder({"z": 0, "a": 1}) == b'{"z":0,"a":1}'
    finally:
        defaults.clear()
        defaults.update(original)


def test_bound_encoder_observes_the_python_facade_helper_replacement(
    bound_encoder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bound_encoder({"value": 7}) == b'{"value":7}'
    monkeypatch.setattr(jsonx, "_native_dumpb", lambda *args: b"caller helper")
    for _ in range(3):
        assert bound_encoder({"value": 7}) == b"caller helper"


def test_bound_encoder_does_not_probe_custom_default_keys(
    bound_encoder: Any,
) -> None:
    effects: list[str] = []

    class Key(str):
        __hash__ = str.__hash__

        def __eq__(self, other: object) -> bool:
            effects.append("key equality")
            return super().__eq__(other)

    defaults = bound_encoder.__kwdefaults__
    original = dict(defaults)
    clone = FunctionType(
        bound_encoder.__code__, bound_encoder.__globals__, bound_encoder.__name__
    )
    clone.__kwdefaults__ = defaults
    try:
        defaults.clear()
        defaults.update({Key("sort_keys"): False, "default": None, "numpy": False})
        expected = clone({"value": 7})
        expected_effects = list(effects)
        effects.clear()
        assert bound_encoder({"value": 7}) == expected
        assert effects == expected_effects
    finally:
        defaults.clear()
        defaults.update(original)


@pytest.mark.parametrize("replacement", ["function", "code"])
def test_reconfiguration_does_not_trust_replaced_encoder_behavior(
    bound_encoder: Any, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    def custom(obj: object, *, sort_keys=False, default=None, numpy=False) -> bytes:
        return b"caller replacement"

    original_code = bound_encoder.__code__
    try:
        if replacement == "function":
            replacement_function = FunctionType(
                custom.__code__, jsonx.__dict__, "dumpb"
            )
            replacement_function.__kwdefaults__ = custom.__kwdefaults__
            monkeypatch.setattr(jsonx, "dumpb", replacement_function)
        else:
            bound_encoder.__code__ = custom.__code__
        native.configure(jsonx.__dict__)
        assert jsonx.dumpb({"value": 7}) == b"caller replacement"
    finally:
        bound_encoder.__code__ = original_code


def test_configuring_copied_globals_does_not_redirect_backend_guards(
    bound_encoder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="actual globals"):
        native.configure(dict(jsonx.__dict__))
    monkeypatch.setattr(jsonx, "_HAVE_ORJSON", False)
    assert native.dumpb({}, False, None, False) is NotImplemented


@pytest.mark.parametrize("kind", ["trace", "profile"])
def test_bound_encoder_runs_python_call_hooks_before_validating_input(
    bound_encoder: Any, kind: str
) -> None:
    value = {"items": [1]}
    effects: list[str] = []

    def hook(frame: Any, event: str, argument: object) -> Any:
        if frame.f_code is bound_encoder.__code__ and event == "call":
            effects.append(event)
            value["items"].append(_Enum.VALUE)
        return hook

    setter = sys.settrace if kind == "trace" else sys.setprofile
    previous = sys.gettrace() if kind == "trace" else sys.getprofile()
    try:
        setter(hook)
        with pytest.raises(TypeError):
            bound_encoder(value)
    finally:
        setter(previous)
    assert effects == ["call"]
    assert bound_encoder({"value": 7}) == b'{"value":7}'
    assert native.dispatch_ready(bound_encoder)


def test_bound_encoder_honors_monitoring_enabled_before_its_next_call(
    bound_encoder: Any,
) -> None:
    monitoring = sys.monitoring
    tool = next(
        (value for value in range(6) if monitoring.get_tool(value) is None), None
    )
    if tool is None:
        pytest.skip("all monitoring tool slots are in use")
    value = {"items": [1]}
    effects: list[str] = []

    def callback(code: object, offset: int) -> None:
        if code is bound_encoder.__code__:
            effects.append("start")
            value["items"].append(_Enum.VALUE)

    monitoring.use_tool_id(tool, "unstd encoder parity")
    try:
        monitoring.register_callback(tool, monitoring.events.PY_START, callback)
        monitoring.set_events(tool, monitoring.events.PY_START)
        with pytest.raises(TypeError):
            bound_encoder(value)
    finally:
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.PY_START, None)
        monitoring.free_tool_id(tool)
    assert effects == ["start"]
    assert bound_encoder({"value": 7}) == b'{"value":7}'
    assert native.dispatch_ready(bound_encoder)
