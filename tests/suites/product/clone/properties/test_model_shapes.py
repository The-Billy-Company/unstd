"""Generated pydantic and ``msgspec.Struct`` trees against their own deep copies.

``BaseModel.model_copy(deep=True)`` and ``copy.deepcopy`` are the authorities for
fields, extras, private attributes and the fields-set; isolation is checked over
every list/dict reachable through those slots. Missing backends skip locally and
fail under ``--require-extras``.
"""

import copy
from types import SimpleNamespace
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd import clone


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=6),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=10,
)
_TAGS = st.lists(st.text(max_size=4), max_size=3)
_EXTRA_NAMES = st.sampled_from(["note", "x_extra", "payload"])


@pytest.fixture(scope="module")
def models() -> SimpleNamespace:
    pydantic = require_backend("pydantic")
    strict = require_backend("unstd.model")

    class Inner(pydantic.BaseModel):
        tags: list[str]
        meta: dict[str, int] = pydantic.Field(default_factory=dict)

    class Outer(pydantic.BaseModel, extra="allow"):
        inner: Inner
        inners: list[Inner] = pydantic.Field(default_factory=list)
        by_key: dict[str, Inner] = pydantic.Field(default_factory=dict)
        maybe: Inner | None = None
        _cache: list[int] = pydantic.PrivateAttr(default_factory=list)

    class Frozen(pydantic.BaseModel, frozen=True):
        items: list[int]
        nested: Inner

    class Wire(strict.StrictModel):
        inner: Inner
        frm: list[str] = pydantic.Field(default_factory=list, alias="from")

    class Value(strict.FrozenModel):
        items: list[int]

    return SimpleNamespace(
        Inner=Inner, Outer=Outer, Frozen=Frozen, Wire=Wire, Value=Value
    )


def _reach(value: object) -> list[object]:
    found, stack = [], [value]
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
        elif hasattr(node, "__pydantic_fields_set__"):
            slots = (node.__dict__, node.__pydantic_extra__, node.__pydantic_private__)
            stack.extend(slot for slot in slots if slot is not None)
        elif hasattr(node, "__struct_fields__"):
            stack.extend(getattr(node, f) for f in node.__struct_fields__)
    return found


def _assert_isolated(actual: object, source: object) -> None:
    def mutable(value: object) -> set[int]:
        return {id(n) for n in _reach(value) if type(n) in (list, dict)}

    assert not mutable(actual) & mutable(source)


def _assert_model_twin(actual: Any, expected: Any) -> None:
    assert type(actual) is type(expected)
    assert actual == expected
    assert actual.model_fields_set == expected.model_fields_set
    assert actual.__pydantic_extra__ == expected.__pydantic_extra__
    assert actual.__pydantic_private__ == expected.__pydantic_private__
    for name, value in actual.__dict__.items():
        if hasattr(value, "model_fields_set"):
            _assert_model_twin(value, expected.__dict__[name])


@given(data=st.data())
def test_models_with_extras_privates_and_partial_fields_set(
    models: SimpleNamespace, data: st.DataObject
) -> None:
    inner = st.builds(
        models.Inner,
        tags=_TAGS,
        meta=st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
    )
    kwargs = data.draw(
        st.fixed_dictionaries(
            {"inner": inner},
            optional={
                "inners": st.lists(inner, max_size=2),
                "by_key": st.dictionaries(st.text(max_size=3), inner, max_size=2),
                "maybe": inner | st.none(),
            },
        )
    )
    extras = data.draw(st.dictionaries(_EXTRA_NAMES, _JSON, max_size=3))
    source = models.Outer(**kwargs, **extras)
    source._cache.extend(data.draw(st.lists(st.integers(), max_size=3)))

    expected = source.model_copy(deep=True)
    actual = clone.deep(source)
    _assert_model_twin(actual, expected)
    _assert_model_twin(clone.deep([source])[0], copy.deepcopy(source))
    _assert_isolated(actual, source)

    for node in _reach(actual):
        if type(node) is list:
            node.append("mutated")
        elif type(node) is dict:
            node["mutated"] = "mutated"
    _assert_model_twin(source, expected)


@given(items=st.lists(st.integers(), max_size=4), tags=_TAGS)
def test_frozen_models_clone_their_mutable_fields(
    models: SimpleNamespace, items: list[int], tags: list[str]
) -> None:
    for source in (
        models.Frozen(items=items, nested=models.Inner(tags=tags)),
        models.Value(items=items),
    ):
        expected = source.model_copy(deep=True)
        actual = clone.deep(source)
        _assert_model_twin(actual, expected)
        _assert_isolated(actual, source)
        actual.items.append(99)
        assert source.items == items


@given(tags=_TAGS, frm=_TAGS, by_alias=st.booleans())
def test_strict_models_keep_alias_population_and_fields_set(
    models: SimpleNamespace, tags: list[str], frm: list[str], by_alias: bool
) -> None:
    payload = {"inner": {"tags": tags}, ("from" if by_alias else "frm"): frm}
    source = models.Wire.model_validate(payload)
    actual = clone.deep(source)
    _assert_model_twin(actual, source.model_copy(deep=True))
    _assert_isolated(actual, source)
    assert actual.model_dump(by_alias=True) == {
        "inner": {"tags": tags, "meta": {}},
        "from": frm,
    }


@pytest.fixture(scope="module")
def structs() -> SimpleNamespace:
    msgspec = require_backend("msgspec")

    class Leaf(msgspec.Struct):
        tags: list[str]

    class Tree(msgspec.Struct, kw_only=True):
        leaves: list[Leaf]
        index: dict[str, Leaf] = msgspec.field(default_factory=dict)

    class Row(msgspec.Struct, array_like=True, frozen=True):
        key: str
        cells: list[int]

    class Tagged(msgspec.Struct, tag=True):
        values: list[int]

    class Untracked(msgspec.Struct, gc=False):
        values: list[int]

    return SimpleNamespace(
        Leaf=Leaf, Tree=Tree, Row=Row, Tagged=Tagged, Untracked=Untracked
    )


@given(data=st.data())
def test_struct_variants_match_deepcopy_and_stay_isolated(
    structs: SimpleNamespace, data: st.DataObject
) -> None:
    ints = st.lists(st.integers(), max_size=3)
    leaf = st.builds(structs.Leaf, _TAGS)
    source = data.draw(
        st.lists(
            st.one_of(
                st.builds(
                    structs.Tree,
                    leaves=st.lists(leaf, max_size=2),
                    index=st.dictionaries(st.text(max_size=3), leaf, max_size=2),
                ),
                st.builds(structs.Row, st.text(max_size=3), ints),
                st.builds(structs.Tagged, ints),
                st.builds(structs.Untracked, ints),
            ),
            max_size=4,
        )
    )
    expected = copy.deepcopy(source)
    actual = clone.deep(source)
    assert actual == expected
    assert [type(a) for a in actual] == [type(e) for e in expected]
    _assert_isolated(actual, source)
    for node in _reach(actual):
        if type(node) is list:
            node.append(0)
    assert source == expected
