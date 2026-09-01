"""Properties for value-faithful, mutation-isolated structural cloning."""

from __future__ import annotations

import copy

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd import clone
from unstd.clone import structural


_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(2**63), 2**63 - 1),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=32),
)
_KEY = st.text(max_size=16)
_TREE = st.recursive(
    _SCALAR,
    lambda children: st.one_of(
        st.lists(children, max_size=6),
        st.dictionaries(_KEY, children, max_size=6),
        st.tuples(children, children),
    ),
    max_leaves=16,
)


@given(value=_TREE)
def test_deep_has_copy_deepcopy_value_and_type_semantics(value: object) -> None:
    """Stdlib ``copy.deepcopy`` is the independent behavioral authority."""
    expected = copy.deepcopy(value)
    actual = clone.deep(value)

    assert actual == expected
    assert type(actual) is type(expected)


@given(payload=st.lists(_TREE, max_size=8))
def test_mutating_clone_cannot_reach_source(payload: list[object]) -> None:
    """A fresh mutable branch in the clone must be reference-isolated."""
    source = {"payload": payload}
    snapshot = copy.deepcopy(source)
    cloned = clone.deep(source)

    assert cloned == snapshot
    assert cloned is not source
    assert cloned["payload"] is not source["payload"]

    cloned["payload"].append({"mutation": [1, 2, 3]})
    assert source == snapshot
    assert cloned != source


@given(value=_TREE)
def test_missing_backend_delegates_to_copy_deepcopy(
    value: object,
) -> None:
    """The base-install fallback calls the general object-graph copier once."""
    deepcopy = copy.deepcopy
    expected = deepcopy(value)
    calls: list[object] = []

    def recording_deepcopy(obj: object) -> object:
        calls.append(obj)
        return deepcopy(obj)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(structural, "_HAVE_MSGSPEC", False)
        monkeypatch.setattr(structural._stdlib, "deepcopy", recording_deepcopy)
        actual = clone.deep(value)
    assert actual == expected
    assert type(actual) is type(expected)
    assert len(calls) == 1
    assert calls[0] is value


@given(payload=st.lists(_SCALAR, max_size=8))
def test_custom_deepcopy_fallback_preserves_user_semantics(
    payload: list[object],
) -> None:
    """Non-structural objects retain their explicit deepcopy protocol."""
    calls: list[object] = []

    class Custom:
        def __init__(self, items: list[object]) -> None:
            self.items = items

        def __deepcopy__(self, memo: dict[int, object]) -> Custom:
            calls.append(memo)
            return Custom(copy.deepcopy(self.items, memo))

    source = Custom(payload)
    cloned = clone.deep(source)

    assert calls
    assert cloned is not source
    assert cloned.items == copy.deepcopy(payload)
    assert cloned.items is not source.items
    cloned.items.append("changed")
    assert source.items == payload
