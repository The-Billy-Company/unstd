"""Generated YAML 1.2 safe values survive the writer and every reader exactly.

The generated Python value is the oracle. A plain ruamel safe loader with the
same pure-Python options reads the emitted text as an independent wrapper check.
"""

from __future__ import annotations

import io
import math

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


# Strings that a resolver, indicator or folding rule could reinterpret.
_AMBIGUOUS = st.sampled_from(
    [
        *("yes", "no", "on", "off", "y", "n", "Yes", "NO"),
        *("null", "Null", "NULL", "~", "true", "False", "TRUE"),
        *("0o17", "0x1F", "012", "1e3", "+1", "-0.0", "1_000", "0."),
        *(".inf", "-.Inf", ".NaN", "2024-01-01", "2024-01-01T00:00:00Z", "12:30"),
        *("<<", "=", "- a", "a: b", "#x", "&a", "*a", "!tag", "%YAML", "---", "..."),
        *("'", '"', "\\", " lead", "trail ", "a\nb", "\n", "\t", "a\r\nb", ""),
        *("a\x85b", "\u2028", "\u2029", "\ufeff", "café", "日本", "😀"),
    ]
)
_TEXT = st.one_of(_AMBIGUOUS, st.text(max_size=12))
_SCALAR = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), _TEXT)
_VALUE = st.recursive(
    _SCALAR,
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(_TEXT, children, max_size=4)
    ),
    max_leaves=12,
)


def _same(actual: object, expected: object) -> bool:
    """Exact type and value, with NaN and signed-zero identity for floats."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, float):
        assert isinstance(actual, float)
        if math.isnan(expected):
            return math.isnan(actual)
        return actual == expected and math.copysign(1, actual) == math.copysign(
            1, expected
        )
    if isinstance(expected, list):
        assert isinstance(actual, list)
        return len(actual) == len(expected) and all(map(_same, actual, expected))
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        return actual.keys() == expected.keys() and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    return actual == expected


def _equivalent(actual: object, expected: object) -> bool:
    """Editable documents may wrap values in subclasses that retain style."""
    if isinstance(expected, bool) or expected is None:
        return actual is expected
    if isinstance(expected, int):
        return (
            isinstance(actual, int)
            and not isinstance(actual, bool)
            and actual == expected
        )
    if isinstance(expected, float):
        return isinstance(actual, float) and _same(float(actual), expected)
    if isinstance(expected, str):
        return isinstance(actual, str) and str(actual) == expected
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(map(_equivalent, actual, expected))
        )
    assert isinstance(expected, dict)
    return (
        isinstance(actual, dict)
        and actual.keys() == expected.keys()
        and all(_equivalent(actual[key], value) for key, value in expected.items())
    )


def _plain_ruamel(text: str) -> object:
    from ruamel.yaml import YAML

    reader = YAML(typ="safe", pure=True)
    reader.allow_duplicate_keys = False
    return reader.load(text)


@given(_VALUE)
@example({"nel": "a\x85b", "floats": [-0.0, math.nan, math.inf], "word": "yes"})
def test_safe_values_round_trip_with_exact_types(value):
    text = yaml.dumps(value)
    assert _same(yaml.loads(text), value), text
    assert _same(yaml.loads(text.encode()), value), text
    assert _same(yaml.load(io.BytesIO(text.encode())), value), text
    assert _same(_plain_ruamel(text), value), text


@given(st.lists(_VALUE, max_size=5))
@example([{"nel": "a\x85b"}, None, [-0.0]])
def test_streams_preserve_document_order_and_count(values):
    stream = io.StringIO()
    yaml.dump_all(values, stream)
    text = stream.getvalue()
    for source in (text, io.BytesIO(text.encode())):
        documents = list(yaml.load_all(source))
        assert len(documents) == len(values), text
        assert all(map(_same, documents, values)), text


@given(_VALUE, _VALUE)
@example(3.403832445554453e16, "a\x85b")
def test_editable_documents_preserve_values_through_edits(seed, added):
    document = yaml.parse(yaml.dumps({"seed": seed}))
    assert _equivalent(document, {"seed": seed})
    assert isinstance(document, dict)
    document["added"] = added
    edited = yaml.dumps(document)
    assert _same(yaml.loads(edited), {"seed": seed, "added": added}), edited
    assert _equivalent(yaml.parse(edited), {"seed": seed, "added": added}), edited
