"""Acceleration is permitted only inside the exact stdlib compatibility domain.

The broad parity suites also cover values which must fall back. This suite
deliberately generates eligible trees, verifies actual backend use, and probes
the eligibility boundary with observable callbacks and backend failures. The
running interpreter's ``json`` remains the independent value/error oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable


_BACKEND_PRESENT = jsonx._HAVE_ORJSON and jsonx._HAVE_MSGSPEC
_TEXT = st.text(st.characters(blacklist_categories=("Cs",)), max_size=24)
_INTEGER = st.integers(min_value=-(2**63), max_value=2**64 - 1)
_SAFE_TREE = st.recursive(
    st.none() | st.booleans() | _INTEGER | _TEXT,
    lambda child: (
        st.lists(child, max_size=4)
        | st.lists(child, max_size=4).map(tuple)
        | st.dictionaries(_TEXT, child, max_size=4)
    ),
    max_leaves=16,
)
_ACCEL_TEXT = st.text(
    st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters=",:"),
    max_size=24,
)
_ACCEL_SCALAR = st.none() | st.booleans() | _INTEGER | _ACCEL_TEXT
_ACCEL_TREE = st.fixed_dictionaries(
    {
        "items": st.lists(_ACCEL_SCALAR, max_size=6),
        "nested": st.dictionaries(_ACCEL_TEXT, _ACCEL_SCALAR, max_size=2),
        "tuple": st.lists(_ACCEL_SCALAR, max_size=2).map(tuple),
    }
)
_OPTIONS = [
    {},
    {"ensure_ascii": False},
    {"separators": (",", ":")},
    {"ensure_ascii": False, "separators": (",", ":")},
    {"indent": 2},
    {"ensure_ascii": False, "indent": 2},
]


@dataclass
class _BackendCalls:
    encoded: list[object] = field(default_factory=list)
    decoded: list[object] = field(default_factory=list)


@pytest.fixture
def backend_calls(monkeypatch: pytest.MonkeyPatch) -> _BackendCalls:
    if not _BACKEND_PRESENT:
        pytest.skip("orjson (`serde` extra) not installed")
    calls = _BackendCalls()
    encode, decode = jsonx.orjson.dumps, jsonx._decode

    def record_encode(value: object, **kwargs: Any) -> bytes:
        calls.encoded.append(value)
        return encode(value, **kwargs)

    def record_decode(value: object, **kwargs: Any) -> object:
        calls.decoded.append(value)
        return decode(value, **kwargs)

    monkeypatch.setattr(jsonx, "_HAVE_ORJSON", True)
    monkeypatch.setattr(jsonx, "_HAVE_MSGSPEC", True)
    monkeypatch.setattr(jsonx, "_encode", record_encode)
    monkeypatch.setattr(jsonx, "_decode", record_decode, raising=False)
    monkeypatch.setattr(jsonx.orjson, "dumps", record_encode)
    return calls


def _same_failure(
    actual_call: Callable[[], object], expected_call: Callable[[], object]
) -> None:
    with pytest.raises(Exception, match=r".") as expected:
        expected_call()
    with pytest.raises(type(expected.value)) as actual:
        actual_call()
    assert type(actual.value) is type(expected.value)
    assert actual.value.args == expected.value.args
    if isinstance(expected.value, json.JSONDecodeError):
        for attribute in ("msg", "doc", "pos", "lineno", "colno"):
            assert getattr(actual.value, attribute) == getattr(
                expected.value, attribute
            )


def _same_value(actual: object, expected: object) -> None:
    assert type(actual) is type(expected)
    if isinstance(expected, float):
        assert isinstance(actual, float)
        assert (
            math.isnan(actual)
            if math.isnan(expected)
            else actual.hex() == expected.hex()
        )
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _same_value(left, right)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert list(actual) == list(expected)
        for key in expected:
            _same_value(actual[key], expected[key])
    else:
        assert actual == expected


@pytest.mark.parametrize("options", _OPTIONS)
@pytest.mark.parametrize("sort_keys", [False, True])
def test_common_encoding_formats_reach_accelerator_with_exact_text(
    backend_calls: _BackendCalls, options: dict[str, object], sort_keys: bool
) -> None:
    value = {"z": [None, True, False, -(2**63), 2**64 - 1], "a": ("ascii", {})}
    assert jsonx.dumps(value, sort_keys=sort_keys, **options) == json.dumps(
        value, sort_keys=sort_keys, **options
    )
    assert len(backend_calls.encoded) == 1


@given(value=_SAFE_TREE)
def test_generated_eligible_trees_keep_exact_formatting(value: object) -> None:
    for options in _OPTIONS:
        for sort_keys in (False, True):
            assert jsonx.dumps(value, sort_keys=sort_keys, **options) == json.dumps(
                value, sort_keys=sort_keys, **options
            )


@given(value=_ACCEL_TREE)
def test_generated_fast_domain_reaches_backend_for_every_example(
    value: object,
) -> None:
    if not _BACKEND_PRESENT:
        pytest.skip("orjson (`serde` extra) not installed")
    calls: list[object] = []
    original = jsonx._encode

    def record(value: object, **kwargs: Any) -> bytes:
        calls.append(value)
        return original(value, **kwargs)

    # A per-example context avoids accumulated Hypothesis fixture state.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(jsonx, "_encode", record)
        for options in _OPTIONS:
            for sort_keys in (False, True):
                assert jsonx.dumps(value, sort_keys=sort_keys, **options) == json.dumps(
                    value, sort_keys=sort_keys, **options
                )
    assert len(calls) == len(_OPTIONS) * 2


def test_eligibility_uses_independent_snapshot_before_entering_backend(
    backend_calls: _BackendCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    value: dict[str, object] = {"items": [1, 2], "nested": {"x": 3}}
    expected = json.dumps(value)
    original = jsonx._encode
    entered: list[object] = []

    def mutate_original(snapshot: object, **kwargs: Any) -> bytes:
        entered.append(snapshot)
        # A backend-supported object introduced after probing must never leak
        # into the encoded tree. Real callbacks are excluded before probing;
        # this injection isolates the validation/serialization boundary.
        items = value["items"]
        assert isinstance(items, list)
        items.append(datetime(2026, 10, 18, tzinfo=UTC))
        nested = value["nested"]
        assert isinstance(nested, dict)
        nested["x"] = math.inf
        return original(snapshot, **kwargs)

    monkeypatch.setattr(jsonx, "_encode", mutate_original)
    assert jsonx.dumps(value) == expected
    assert len(entered) == 1
    assert entered[0] is not value


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("indent", [None, 2])
def test_string_escaping_and_spacing_are_lexically_aware(
    backend_calls: _BackendCalls, ensure_ascii: bool, indent: int | None
) -> None:
    value = {
        'punctuation ,:"\\[{}]': [
            'comma,colon:quote"backslash\\',
            "\b\f\n\r\t\x00\x01\x1f\x7f",
            "é😀\u2028\u2029\ufffe\uffff",
        ],
        "empty": [[], {}],
    }
    options = {"ensure_ascii": ensure_ascii, "indent": indent}
    assert jsonx.dumps(value, **options) == json.dumps(value, **options)


@pytest.mark.parametrize(
    "value",
    [
        [0.0],
        [-0.0],
        [1e-5],
        [5e-324],
        [math.nan],
        [math.inf],
        [-math.inf],
        [2**64],
        [-(2**63) - 1],
        [2**1024],
        {1: "coerced key"},
    ],
)
def test_unsafe_values_never_reach_encoder(
    backend_calls: _BackendCalls, value: object
) -> None:
    assert jsonx.dumps(value) == json.dumps(value)
    assert backend_calls.encoded == []


def test_integer_sequence_probe_does_not_hash_or_compare_user_classes(
    backend_calls: _BackendCalls,
) -> None:
    class Meta(type):
        def __hash__(cls) -> int:
            pytest.fail("eligibility invoked a user metaclass hash")

        def __eq__(cls, other: object) -> bool:
            pytest.fail("eligibility invoked user metaclass equality")

    class Unsupported(metaclass=Meta):
        pass

    value = [Unsupported()] * 8
    _same_failure(lambda: jsonx.dumps(value), lambda: json.dumps(value))
    assert backend_calls.encoded == []


@pytest.mark.parametrize("value", [["\ud800"], ["\ud800\udfff"]])
@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_surrogates_defer_to_stdlib_encoder_before_exposing_a_result(
    backend_calls: _BackendCalls,
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    ensure_ascii: bool,
) -> None:
    expected = json.dumps(value, ensure_ascii=ensure_ascii)
    reference_encode = json.JSONEncoder.encode
    fallbacks: list[object] = []

    def fallback(encoder: json.JSONEncoder, value: object) -> str:
        fallbacks.append(value)
        return reference_encode(encoder, value)

    monkeypatch.setattr(json.JSONEncoder, "encode", fallback)
    assert jsonx.dumps(value, ensure_ascii=ensure_ascii) == expected
    assert fallbacks == [value]


@pytest.mark.parametrize("kind", [str, int, float, list, tuple, dict])
def test_subclasses_never_reach_encoder(
    backend_calls: _BackendCalls, kind: type[Any]
) -> None:
    subclass = type("Custom", (kind,), {})
    value = subclass({"x": 1}) if kind is dict else subclass()
    assert jsonx.dumps([value]) == json.dumps([value])
    assert backend_calls.encoded == []


@pytest.mark.parametrize(
    "keyword", ["skipkeys", "ensure_ascii", "check_circular", "allow_nan", "sort_keys"]
)
def test_nonbuiltin_option_truthiness_is_never_evaluated_speculatively(
    backend_calls: _BackendCalls, keyword: str
) -> None:
    def run(dumps: Callable[..., str]) -> tuple[str, list[str]]:
        effects: list[str] = []
        value = {"x": [1, 2]}

        class Flag:
            def __bool__(self) -> bool:
                effects.append("bool")
                value["x"].append(len(effects))
                return True

            def __eq__(self, other: object) -> bool:
                effects.append("eq")
                return False

        return dumps(value, **{keyword: Flag()}), effects

    assert run(jsonx.dumps) == run(json.dumps)
    assert backend_calls.encoded == []


@pytest.mark.parametrize("keyword", ["indent", "separators"])
def test_custom_formatting_options_are_not_inspected_by_eligibility_probe(
    backend_calls: _BackendCalls, keyword: str
) -> None:
    def run(dumps: Callable[..., str]) -> tuple[str, list[str]]:
        effects: list[str] = []

        class Indent(int):
            def __eq__(self, other: object) -> bool:
                effects.append("indent equality")
                return super().__eq__(other)

        class Separators(tuple):
            def __iter__(self) -> Any:
                effects.append("separator iteration")
                return super().__iter__()

            def __eq__(self, other: object) -> bool:
                effects.append("separator equality")
                return super().__eq__(other)

        option = Indent(2) if keyword == "indent" else Separators((",", ":"))
        return dumps({"x": [1, 2]}, **{keyword: option}), effects

    assert run(jsonx.dumps) == run(json.dumps)
    assert backend_calls.encoded == []


def test_default_mutation_and_reentrancy_remain_stdlib_owned(
    backend_calls: _BackendCalls,
) -> None:
    def run(dumps: Callable[..., str]) -> tuple[str, list[object]]:
        unknown = object()
        shared = [1]
        value = [shared, unknown, shared]
        effects: list[object] = []

        def default(obj: object) -> object:
            effects.append(obj is unknown)
            shared.append(2)
            # This nested invocation is separately safe and may accelerate.
            effects.append(dumps({"nested": [3, 4]}))
            return shared

        return dumps(value, default=default), effects

    assert run(jsonx.dumps) == run(json.dumps)
    assert len(backend_calls.encoded) <= 1
    assert all(type(value) is dict for value in backend_calls.encoded)


def test_explicit_default_disables_backend_even_when_no_conversion_is_needed(
    backend_calls: _BackendCalls,
) -> None:
    def default(value: object) -> object:
        pytest.fail(f"default unexpectedly called for {value!r}")

    assert jsonx.dumps({"x": [1]}, default=default) == '{"x": [1]}'
    assert backend_calls.encoded == []


def test_custom_encoder_and_unknown_keywords_are_not_hidden_by_fast_path(
    backend_calls: _BackendCalls,
) -> None:
    marker = object()
    calls: list[object] = []

    class Encoder(json.JSONEncoder):
        def __init__(self, *, marker: object, **kwargs: Any) -> None:
            calls.append(marker)

        def encode(self, value: object) -> str:
            calls.append(value)
            return "custom output"

    value = {"x": 1}
    assert jsonx.dumps(value, cls=Encoder, marker=marker) == "custom output"
    assert calls == [marker, value]
    _same_failure(
        lambda: jsonx.dumps(value, unrecognized=True),
        lambda: json.dumps(value, unrecognized=True),
    )
    assert backend_calls.encoded == []


def test_circular_and_deep_inputs_use_stdlib_error_behavior(
    backend_calls: _BackendCalls,
) -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    _same_failure(lambda: jsonx.dumps(cycle), lambda: json.dumps(cycle))
    deep: object = 0
    for _ in range(100):
        deep = [deep]
    assert jsonx.dumps(deep) == json.dumps(deep)
    assert backend_calls.encoded == []


@pytest.mark.parametrize(
    "document",
    [
        '{"z": [true, null, -9223372036854775808, 18446744073709551615]}',
        ' [ "1e999 NaN 18446744073709551616", {"x":1,"x":2} ] ',
    ],
)
def test_integer_documents_reach_decoder_with_exact_values(
    backend_calls: _BackendCalls, document: str
) -> None:
    _same_value(jsonx.loads(document), json.loads(document))
    assert len(backend_calls.decoded) == 1


@given(value=_SAFE_TREE)
def test_generated_integer_documents_keep_values_and_types(value: object) -> None:
    for ensure_ascii in (False, True):
        document = json.dumps(value, ensure_ascii=ensure_ascii)
        _same_value(jsonx.loads(document), json.loads(document))


@pytest.mark.parametrize(
    "document",
    [
        "[NaN]",
        "[Infinity]",
        "[-Infinity]",
        r'["\ud800"]',
        r'["\udfff"]',
        '["\ud800"]',
        '["\ud800\udfff"]',
    ],
)
def test_unsafe_documents_defer_to_stdlib_before_exposing_a_result(
    backend_calls: _BackendCalls,
    monkeypatch: pytest.MonkeyPatch,
    document: str,
) -> None:
    reference_loads = json.loads
    expected = reference_loads(document)
    fallback_calls: list[object] = []

    def fallback(value: object, **kwargs: Any) -> object:
        fallback_calls.append(value)
        return reference_loads(value, **kwargs)

    monkeypatch.setattr(jsonx._stdlib, "loads", fallback)
    _same_value(jsonx.loads(document), expected)
    assert fallback_calls == [document]


@pytest.mark.parametrize(
    "keyword",
    ["object_hook", "object_pairs_hook", "parse_int", "parse_float", "parse_constant"],
)
def test_decode_hooks_never_speculatively_reach_backend(
    backend_calls: _BackendCalls, keyword: str
) -> None:
    document = '{"x": 1}'
    effects: list[object] = []

    def hook(value: object) -> object:
        effects.append(value)
        return value

    _same_value(
        jsonx.loads(document, **{keyword: hook}),
        json.loads(document, **{keyword: hook}),
    )
    assert backend_calls.decoded == []


@pytest.mark.parametrize(
    "document", ['{"é": 1, "x": }', "[1,]", '"unterminated', "{} {}"]
)
def test_invalid_safe_domain_documents_keep_stdlib_error_metadata(
    backend_calls: _BackendCalls, document: str
) -> None:
    _same_failure(lambda: jsonx.loads(document), lambda: json.loads(document))


@pytest.mark.parametrize("recursion_error", [False, True])
def test_accelerated_encoder_failure_retries_stdlib_without_wrapping(
    backend_calls: _BackendCalls,
    monkeypatch: pytest.MonkeyPatch,
    recursion_error: bool,
) -> None:
    attempts: list[object] = []

    def fail(value: object, **kwargs: Any) -> bytes:
        attempts.append(value)
        failure = (
            RecursionError("backend limit")
            if recursion_error
            else jsonx.orjson.JSONEncodeError("backend refusal")
        )
        raise failure

    monkeypatch.setattr(jsonx, "_encode", fail)
    monkeypatch.setattr(jsonx.orjson, "dumps", fail)
    value = {"x": [1, 2, 3]}
    assert jsonx.dumps(value) == json.dumps(value)
    assert len(attempts) == 1


@pytest.mark.parametrize("document", ['{"x": [1, 2]}', '{"x": [1, 2,]}'])
@pytest.mark.parametrize("recursion_error", [False, True])
def test_accelerated_decoder_failure_defers_to_stdlib_result_and_diagnostics(
    backend_calls: _BackendCalls,
    monkeypatch: pytest.MonkeyPatch,
    document: str,
    recursion_error: bool,
) -> None:
    attempts: list[object] = []

    def fail(value: object, **kwargs: Any) -> object:
        attempts.append(value)
        failure = (
            RecursionError("backend limit")
            if recursion_error
            else jsonx.msgspec.DecodeError("backend refusal")
        )
        raise failure

    monkeypatch.setattr(jsonx, "_decode", fail, raising=False)
    if document.endswith(",]}"):
        _same_failure(lambda: jsonx.loads(document), lambda: json.loads(document))
    else:
        _same_value(jsonx.loads(document), json.loads(document))
        assert len(attempts) == 1


def test_custom_decoder_never_reaches_accelerator(
    backend_calls: _BackendCalls,
) -> None:
    sentinel = object()
    calls: list[object] = []

    class Decoder(json.JSONDecoder):
        def __init__(self, *, marker: object, **kwargs: Any) -> None:
            calls.append(marker)

        def decode(self, document: str) -> object:
            calls.append(document)
            return sentinel

    assert jsonx.loads('{"x": 1}', cls=Decoder, marker=sentinel) is sentinel
    assert calls == [sentinel, '{"x": 1}']
    assert backend_calls.decoded == []


def test_custom_string_input_methods_have_only_stdlib_effects(
    backend_calls: _BackendCalls,
) -> None:
    def run(loads: Callable[..., object]) -> tuple[object, list[str]]:
        calls: list[str] = []

        class Text(str):
            def startswith(self, *args: Any, **kwargs: Any) -> bool:
                calls.append("startswith")
                return super().startswith(*args, **kwargs)

            def __iter__(self) -> Any:
                calls.append("iter")
                return super().__iter__()

        return loads(Text('{"x": [1, 2]}')), calls

    assert run(jsonx.loads) == run(json.loads)
    assert backend_calls.decoded == []


def test_acceleration_preserves_stdlib_recursion_failures() -> None:
    # Keep the runner's own recursion limit and stack independent of this
    # deliberately exhausted worker. Additional wrapper frames may reject a
    # little earlier; successful acceleration must never evade a stdlib limit.
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

        violations = []
        for limit in (120, 180):
            sys.setrecursionlimit(limit)
            for nesting in (1, 16, 64):
                document = "[" * nesting + "0" + "]" * nesting
                value = json.loads(document)
                for name, reference, actual, argument in (
                    ("loads", json.loads, jsonx.loads, document),
                    ("binary loads", json.loads, jsonx.loads, document.encode()),
                    ("dumps", json.dumps, jsonx.dumps, value),
                    (
                        "indented dumps",
                        partial(json.dumps, indent=2),
                        partial(jsonx.dumps, indent=2),
                        value,
                    ),
                    (
                        "compact dumps",
                        partial(json.dumps, separators=(",", ":")),
                        partial(jsonx.dumps, separators=(",", ":")),
                        value,
                    ),
                ):
                    for depth in range(limit - 14, limit):
                        expected = accepted(reference, argument, depth)
                        observed = accepted(actual, argument, depth)
                        if observed and not expected:
                            violations.append((name, limit, nesting, depth))
        assert not violations, violations
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed source in an isolated Python worker
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
