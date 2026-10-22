"""Generated schemas, valid and invalid values for the typed ``Codec``.

Schemas are built with ``msgspec.defstruct`` from a small declared-type table.
Two oracles apply: raw ``msgspec.json`` objects with the same options (wrapper
faithfulness) and an independently built stdlib-``json`` model of each value
(UUIDs canonical, Decimals as strings), which fixes the semantic payload and —
for float-free values — the exact sorted compact bytes.
"""

from __future__ import annotations

from decimal import Decimal
import json
import re
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend


msgspec = require_backend("msgspec")
from unstd.serde import structs  # noqa: E402


_INT = st.integers(-(2**63), 2**64 - 1)
_TEXT = st.text(st.characters(exclude_categories=("Cs",)), max_size=12)

# kind -> (declared type, value strategy, stdlib-json model, wrong-typed JSON value)
TYPES: dict[str, tuple[object, st.SearchStrategy[object], object, object]] = {
    "int": (int, _INT, int, "1"),
    "str": (str, _TEXT, str, 1),
    "bool": (bool, st.booleans(), bool, 0),
    "float": (float, st.floats(allow_nan=False, allow_infinity=False), float, "1.5"),
    "decimal": (Decimal, st.decimals(allow_nan=False, allow_infinity=False), str, True),
    "uuid": (UUID, st.uuids(), str, 1),
    "opt_int": (int | None, st.none() | _INT, lambda v: v, "x"),
    "ints": (list[int], st.lists(_INT, max_size=4), list, {}),
    "table": (dict[str, int], st.dictionaries(_TEXT, _INT, max_size=4), dict, []),
}
FLOATLESS = tuple(kind for kind in TYPES if kind != "float")
_NAMES = st.lists(
    st.from_regex(r"f_[a-z0-9]{1,6}", fullmatch=True),
    min_size=1,
    max_size=5,
    unique=True,
)


@st.composite
def cases(draw: st.DrawFn, kinds: tuple[str, ...] = tuple(TYPES)) -> tuple:
    fields = {name: draw(st.sampled_from(kinds)) for name in draw(_NAMES)}
    spec = msgspec.defstruct("Generated", [(n, TYPES[k][0]) for n, k in fields.items()])
    values = {name: draw(TYPES[kind][1]) for name, kind in fields.items()}
    model = {name: TYPES[fields[name]][2](v) for name, v in values.items()}  # type: ignore[operator]
    return spec, fields, spec(**values), model


@given(cases())
def test_valid_values_match_raw_msgspec_and_stdlib_model(case: tuple) -> None:
    spec, fields, value, model = case
    codec = structs.Codec(spec)
    encoded = codec.encode(value)
    assert encoded == msgspec.json.Encoder().encode(value)
    assert json.loads(encoded) == model
    assert list(json.loads(encoded)) == list(fields)  # declaration order
    decoded = codec.decode(encoded)
    assert decoded == value
    for name in fields:
        assert type(getattr(decoded, name)) is type(getattr(value, name))


@given(cases(FLOATLESS), st.randoms(use_true_random=False))
def test_sorted_order_is_byte_identical_to_sorted_stdlib_json(case: tuple, rng) -> None:
    spec, _, value, model = case
    expected = json.dumps(
        model, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    codec = structs.Codec(spec, order="sorted")
    assert codec.encode(value) == expected.encode()
    assert codec.encode(value) == msgspec.json.Encoder(order="sorted").encode(value)
    shuffled = list(model.items())
    rng.shuffle(shuffled)
    plain = structs.Codec(dict, order="deterministic")
    assert plain.encode(dict(shuffled)) == plain.encode(model)


@given(cases(), st.data())
def test_wrong_typed_field_is_rejected_at_its_path(
    case: tuple, data: st.DataObject
) -> None:
    spec, fields, value, _model = case
    target = data.draw(st.sampled_from(list(fields)))
    raw = json.dumps(
        json.loads(msgspec.json.encode(value)) | {target: TYPES[fields[target]][3]}
    )
    codec = structs.Codec(spec)
    with pytest.raises(msgspec.ValidationError) as caught:
        codec.decode(raw)
    assert str(caught.value).endswith(f"- at `$.{target}`")
    with pytest.raises(msgspec.ValidationError) as raw_caught:
        msgspec.json.Decoder(spec).decode(raw)
    assert str(raw_caught.value) == str(caught.value)
    sentinel = object()
    assert codec.decode_or(raw, sentinel) is sentinel  # type: ignore[arg-type]
    assert codec.decode(codec.encode(value)) == value


@given(cases(), st.from_regex(r"g_[a-z]{1,6}", fullmatch=True))
def test_schema_mismatch_names_the_offending_field(case: tuple, extra: str) -> None:
    spec, fields, value, _ = case
    encoded = structs.Codec(spec).encode(value)
    wider = msgspec.defstruct(
        "Wider", [(n, TYPES[k][0]) for n, k in fields.items()] + [(extra, int)]
    )
    with pytest.raises(msgspec.ValidationError) as caught:
        structs.Codec(wider).decode(encoded)
    assert str(caught.value) == f"Object missing required field `{extra}`"
    closed = msgspec.defstruct("Closed", [(extra, int, 0)], forbid_unknown_fields=True)
    with pytest.raises(msgspec.ValidationError) as caught:
        structs.Codec(closed).decode(encoded)
    assert str(caught.value) == f"Object contains unknown field `{next(iter(fields))}`"


@given(st.lists(_INT | _TEXT | st.booleans(), max_size=6), st.booleans())
def test_lax_decoding_matches_raw_decoder(values: list, strict: bool) -> None:
    raw = json.dumps([str(v) for v in values])
    codec = structs.Codec(list[int], strict=strict)
    try:
        expected = msgspec.json.Decoder(list[int], strict=strict).decode(raw)
    except msgspec.ValidationError as error:
        with pytest.raises(msgspec.ValidationError, match=f"^{re.escape(str(error))}$"):
            codec.decode(raw)
    else:
        assert codec.decode(raw) == expected
        assert all(type(v) is int for v in expected)
        assert expected == [int(s) for s in json.loads(raw)]
