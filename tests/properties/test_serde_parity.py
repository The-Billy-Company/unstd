"""Cross-implementation parity for ``unstd.serde.jsonx`` against the JSON grammar.

Law (positive): ``jsonx`` is a stdlib-``json``-faithful codec, so on the shared
value subset (RFC 8259 JSON of finite floats, 64-bit-range ints, str keys, and
surrogate-free text) it must interoperate value-for-value with the **stdlib
``json`` module** in *both* directions — ``json`` parses what ``jsonx`` emits
back to the original value, ``jsonx`` parses what ``json`` emits back to the
original value, and the two decoders agree on identical bytes.

Authority: the Python stdlib ``json`` module — an independent reference
implementation of the same grammar, not the code under test. ``jsonx`` wraps
orjson (or falls back to stdlib); stdlib ``json`` is a wholly separate parser,
so agreement is a real contract, never a self-oracle.

Law (adverse): a structurally malformed document (a well-formed container with
its closing bracket removed) is rejected by ``jsonx`` exactly as the stdlib
authority rejects it — ``jsonx.JSONDecodeError`` (which orjson subclasses).
"""

from __future__ import annotations

import json

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx


# ── shared JSON value subset (the documented parity domain of jsonx) ─────────────
# Surrogate-free text (orjson rejects lone surrogates that stdlib would escape);
# ints inside orjson's signed/unsigned 64-bit codec range; finite floats only
# (NaN/Infinity are the documented jsonx↔stdlib divergence, excluded here).
_TEXT = st.text(st.characters(blacklist_categories=("Cs",)), max_size=40)
_KEY = st.text(st.characters(blacklist_categories=("Cs",)), max_size=20)
_INT = st.integers(min_value=-(2**63), max_value=2**64 - 1)
_FLOAT = st.floats(allow_nan=False, allow_infinity=False)
_SCALAR = st.none() | st.booleans() | _INT | _FLOAT | _TEXT
_JSON = st.recursive(
    _SCALAR,
    lambda children: (
        st.lists(children, max_size=6) | st.dictionaries(_KEY, children, max_size=6)
    ),
    max_leaves=25,
)
# Non-scalar top level, so removing the final byte always drops a real bracket.
_CONTAINER = st.lists(_JSON, max_size=5) | st.dictionaries(_KEY, _JSON, max_size=5)


@given(value=_JSON)
@example(value={})
@example(value=[])
@example(value={"k": [1, 2.5, "é", None, True]})
def test_jsonx_interoperates_with_stdlib_json_both_directions(value: object) -> None:
    """Stdlib ``json`` is the independent authority; the codecs must agree."""
    jsonx_bytes = jsonx.dumps(value)
    stdlib_text = json.dumps(value)

    # jsonx encodes → stdlib decodes → original value (and the reverse).
    assert json.loads(jsonx_bytes) == value
    assert jsonx.loads(stdlib_text) == value
    # Both decoders agree on the very same bytes.
    assert jsonx.loads(stdlib_text) == json.loads(stdlib_text)
    # Self round-trip closes the loop for the accelerated encoder+decoder pair.
    assert jsonx.loads(jsonx_bytes) == value


@given(container=_CONTAINER)
@example(container={})
@example(container=[])
def test_jsonx_rejects_truncated_documents_like_stdlib(container: object) -> None:
    """A container missing its closing bracket is rejected by both parsers."""
    complete = json.dumps(container)
    # Positive control: the authority accepts the intact document (so a
    # "always-raises" stub would fail this, not just the truncated raises).
    assert json.loads(complete) == container
    assert jsonx.loads(complete) == container

    truncated = complete[:-1]  # drop the closing } or ]
    assert len(truncated) == len(complete) - 1

    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)  # the independent authority rejects it
    with pytest.raises(jsonx.JSONDecodeError) as jx_exc:
        jsonx.loads(truncated)  # …and so must jsonx
    # Contract: jsonx.JSONDecodeError is the stdlib class (or an orjson subclass).
    assert isinstance(jx_exc.value, json.JSONDecodeError)
