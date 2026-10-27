"""Model acceleration preserves allocation and observable state-copy protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import pytest

from tests.support.environment.backends import require_backend
from unstd import clone
from unstd.clone import structural


if TYPE_CHECKING:
    from pydantic import BaseModel, PrivateAttr
else:
    _pydantic = require_backend("pydantic")
    BaseModel = _pydantic.BaseModel
    PrivateAttr = _pydantic.PrivateAttr


@pytest.fixture(params=[False, True], ids=["python", "native"])
def model_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.param:
        if structural._native_walk_graph is None:
            pytest.skip("native graph extension is not built")
    else:
        monkeypatch.setattr(structural, "_native_clone", None)


def test_model_custom_allocation_is_not_bypassed(model_backend: None) -> None:
    calls: list[type[Model]] = []

    class Model(BaseModel):
        values: list[int]

        def __new__(cls, *args: object, **kwargs: object) -> Self:
            calls.append(cls)
            return object.__new__(cls)

    source = Model(values=[1, 2])
    calls.clear()
    copied = clone.deep([source, source])
    assert calls == [Model]
    assert copied[0] is copied[1]
    assert copied[0] is not source
    assert copied[0].values == source.values
    assert copied[0].values is not source.values


def test_model_state_root_honors_atom_memo_override(model_backend: None) -> None:
    class Hook:
        def __deepcopy__(self, memo: dict[int, object]) -> str:
            memo[id(13)] = "memo replacement"
            return "copied"

    class Model(BaseModel):
        value: object

        def __getattribute__(self, name: str) -> object:
            if name == "__pydantic_fields_set__":
                return 13
            return super().__getattribute__(name)

    source = Model(value=Hook())
    copied = clone.deep(source)
    assert (
        object.__getattribute__(copied, "__pydantic_fields_set__") == "memo replacement"
    )


def test_model_state_memo_hit_keeps_membership_and_getitem(
    model_backend: None,
) -> None:
    calls: list[int] = []
    failure = LookupError("second state lookup")
    source: Model

    class Key:
        def __hash__(self) -> int:
            return id(source.__pydantic_fields_set__)

        def __eq__(self, other: object) -> bool:
            if type(other) is int and other == id(source.__pydantic_fields_set__):
                calls.append(other)
                if len(calls) == 2:
                    raise failure
                return True
            return False

    class Hook:
        def __deepcopy__(self, memo: dict[int, object]) -> str:
            # Python permits arbitrary hashable keys in a copy memo. Keep this
            # intentionally foreign key at the boundary of its typed contract.
            memo[Key()] = "override"  # type: ignore[index]
            return "copied"

    class Model(BaseModel):
        value: object

    source = Model(value=Hook())
    with pytest.raises(LookupError) as caught:
        clone.deep(source)
    assert caught.value is failure
    assert calls == [id(source.__pydantic_fields_set__)] * 2


def test_model_state_attributes_are_read_in_order(model_backend: None) -> None:
    reads: list[str] = []
    original: Model | None = None
    names = {
        "__dict__",
        "__pydantic_extra__",
        "__pydantic_fields_set__",
        "__pydantic_private__",
    }

    class Model(BaseModel, extra="allow"):
        values: list[int]
        _cache: list[int] = PrivateAttr(default_factory=list)

        def __getattribute__(self, name: str) -> object:
            if self is original and name in names:
                reads.append(name)
            return super().__getattribute__(name)

    source = Model(values=[1], note=[2])
    source._cache = [3]
    original = source
    copied = clone.deep(source)
    original = None
    assert reads == [
        "__dict__",
        "__pydantic_extra__",
        "__pydantic_fields_set__",
        "__pydantic_private__",
    ]
    assert copied == source
    assert copied.values is not source.values
    assert copied._cache is not source._cache


@pytest.mark.parametrize("where", ["field", "extra", "private"])
def test_model_hooks_observe_only_completed_state_phases(
    model_backend: None, where: str
) -> None:
    observed: list[tuple[bool, bool, bool, bool]] = []
    source: Model

    class Hook:
        def __deepcopy__(self, memo: dict[int, object]) -> str:
            twin = memo[id(source)]
            states: list[bool] = []
            for name in (
                "__dict__",
                "__pydantic_extra__",
                "__pydantic_fields_set__",
                "__pydantic_private__",
            ):
                try:
                    value = object.__getattribute__(twin, name)
                except AttributeError:
                    states.append(False)
                else:
                    states.append(bool(value) if name == "__dict__" else True)
            observed.append((states[0], states[1], states[2], states[3]))
            return "copied"

    class Model(BaseModel, extra="allow"):
        field: object = None
        _cache: object = PrivateAttr(default=None)

    source = Model(field=Hook()) if where == "field" else Model()
    if where == "extra":
        source = Model(note=Hook())
    elif where == "private":
        source._cache = Hook()
    copied = clone.deep(source)
    assert observed == [
        (False, False, False, False)
        if where == "field"
        else (True, False, False, False)
        if where == "extra"
        else (True, True, True, False)
    ]
    assert copied is not source


def test_model_constructor_change_is_observed_by_later_records(
    model_backend: None,
) -> None:
    calls: list[str] = []

    class Model(BaseModel):
        value: object

    def changed_new(cls: type[Model], *_args: object, **_kwargs: object) -> Model:
        calls.append("changed")
        return object.__new__(cls)

    class Hook:
        def __deepcopy__(self, _memo: dict[int, object]) -> str:
            Model.__new__ = staticmethod(changed_new)
            return "copied"

    first, second = Model(value=Hook()), Model(value=[1])
    copied = clone.deep([first, second])
    assert calls == ["changed"]
    assert copied[0].value == "copied"
    assert copied[1].value == [1]
    assert copied[1].value is not second.value


def test_model_state_errors_do_not_retry_allocation(model_backend: None) -> None:
    calls: list[str] = []
    original: Model | None = None
    failure = LookupError("original model state is unavailable")

    class Model(BaseModel):
        value: int

        def __new__(cls, *args: object, **kwargs: object) -> Self:
            calls.append("new")
            return object.__new__(cls)

        def __getattribute__(self, name: str) -> object:
            if self is original and name == "__dict__":
                raise failure
            return super().__getattribute__(name)

    source = Model(value=1)
    original = source
    calls.clear()
    with pytest.raises(LookupError) as observed:
        clone.deep(source)
    assert observed.value is failure
    assert calls == ["new"]


def test_model_metaclass_hash_callbacks_keep_the_python_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Model(BaseModel):
        values: list[int]

    source = Model(values=[1, 2])
    calls: list[str] = []

    def record_hash(cls: type[object]) -> int:
        calls.append("hash")
        return type.__hash__(cls)

    traces: list[list[str]] = []
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            patch.setattr(type(Model), "__hash__", record_hash)
            if not enabled:
                patch.setattr(structural, "_native_clone", None)
            clone.deep(source)
            calls.clear()
            copied = clone.deep(source)
            traces.append(list(calls))
        assert copied == source
        assert copied.values is not source.values
    assert traces[0]
    assert traces[0] == traces[1]


def test_model_continuation_ignores_a_foreign_handler_hint() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    if not getattr(native, "MODEL_CONTINUATION", 0):
        pytest.skip("native model continuations are not built")

    class Model(BaseModel):
        values: list[int]

    def foreign(_source: object, _memo: dict[int, object]) -> object:
        raise AssertionError

    source = Model(values=[1, 2])
    clone.deep(source)
    copied = native.clone_walk_canonical_graph(
        source, structural._walk, structural._record_admit, {}, foreign
    )
    assert type(copied) is Model
    assert copied == source
    assert copied.values is not source.values


def test_model_handler_code_changes_are_observed_by_later_records(
    model_backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Model(BaseModel):
        value: object

    def replaced(source: Model, _memo: dict[int, object]) -> tuple[str, object]:
        return "changed model", source.value

    class Hook:
        def __deepcopy__(self, _memo: dict[int, object]) -> str:
            monkeypatch.setattr(structural._model, "__code__", replaced.__code__)
            return "copied"

    first, second = Model(value=Hook()), Model(value=42)
    clone.deep(Model(value=None))
    copied = clone.deep([first, second])
    assert type(copied[0]) is Model
    assert copied[0].value == "copied"
    assert copied[1] == ("changed model", 42)


@pytest.mark.parametrize("when", ["before", "field"])
def test_model_state_walker_changes_are_observed_at_state_boundaries(
    model_backend: None, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    calls: list[int] = []
    original = structural._walk_data

    def replaced(source: object, memo: dict[int, object]) -> object:
        calls.append(id(source))
        return original(source, memo)

    class Hook:
        def __deepcopy__(self, _memo: dict[int, object]) -> str:
            monkeypatch.setattr(structural, "_walk_data", replaced)
            return "copied"

    class Model(BaseModel, extra="allow"):
        value: object

    source = Model(value=Hook() if when == "field" else [1], note=[2])
    clone.deep(Model(value=None))
    if when == "before":
        monkeypatch.setattr(structural, "_walk_data", replaced)
    copied = clone.deep(source)
    expected = [id(source.__pydantic_extra__), id(source.__pydantic_fields_set__)]
    if when == "before":
        expected.insert(0, id(source.__dict__))
    assert calls == expected
    assert copied is not source
    assert copied.value == ("copied" if when == "field" else [1])
    assert copied.model_extra == source.model_extra
    assert copied.model_extra is not source.model_extra


@pytest.mark.parametrize(
    "changed",
    [
        "_walk",
        "_record_admit",
        "_native_walk_graph",
        "_walk_data_after_memo",
        "body-code",
        "_model",
        "_native_clone",
    ],
)
def test_model_state_memo_miss_reads_current_dispatch_globals(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    native_walk = structural._native_walk_graph
    if native_walk is None:
        pytest.skip("native model continuations are not built")
    sentinel = object()
    memo: dict[int, object] = {}
    calls: list[int] = []
    state: ForeignState
    active_patch: pytest.MonkeyPatch

    class ForeignMeta(type):
        def __hash__(cls) -> int:
            return type.__hash__(cls)

    class ForeignState(metaclass=ForeignMeta if changed == "_walk" else type):
        def __deepcopy__(self, _memo: dict[int, object]) -> str:
            return "original state"

    def replacement(*_args: object) -> object:
        return sentinel

    def changed_admit(_cls: type) -> tuple[bool, object]:
        return False, replacement

    def changed_body(_source: object, _memo: dict[int, object]) -> str:
        return "changed body"

    class Key:
        def __hash__(self) -> int:
            return id(state)

        def __eq__(self, other: object) -> bool:
            if type(other) is int and other == id(state):
                calls.append(other)
                if len(calls) == 1:
                    if changed == "body-code":
                        active_patch.setattr(
                            structural._walk_data_after_memo,
                            "__code__",
                            changed_body.__code__,
                        )
                    else:
                        active_patch.setattr(
                            structural,
                            changed,
                            None
                            if changed == "_native_clone"
                            else changed_admit
                            if changed == "_record_admit"
                            else replacement,
                        )
            return False

    key = Key()

    class Hook:
        def __deepcopy__(self, graph_memo: dict[int, object]) -> str:
            # Exercise Python's foreign memo-key boundary without widening
            # the production memo's integer-key contract.
            graph_memo[key] = "unused"  # type: ignore[index]
            return "copied"

    class Model(BaseModel):
        value: object

        def __getattribute__(self, name: str) -> object:
            if name == "__pydantic_fields_set__":
                return state
            return super().__getattribute__(name)

    state = ForeignState()
    source = Model(value=Hook())
    clone.deep(Model(value=None))
    results: list[tuple[object, list[int]]] = []
    for enabled in (False, True):
        with monkeypatch.context() as active_patch:
            active_patch.setattr(structural, "_NATIVE_MODEL_CONTINUATION", int(enabled))
            memo.clear()
            calls.clear()
            copied = (
                native_walk(
                    source,
                    structural._walk,
                    structural._record_admit,
                    memo,
                    structural._model,
                )
                if enabled
                else native_walk(
                    source, structural._walk, structural._record_admit, memo
                )
            )
            copied_state = object.__getattribute__(copied, "__pydantic_fields_set__")
            results.append((copied_state, calls.copy()))
    if changed in {"_model", "_native_clone"}:
        assert results[0][0] == "original state"
    elif changed == "body-code":
        assert results[0][0] == "changed body"
    else:
        assert results[0][0] is sentinel
    assert results[1] == results[0]
