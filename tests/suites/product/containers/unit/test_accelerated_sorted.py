"""Native sorted subclasses preserve upstream state, factories and protocols."""

from __future__ import annotations

import inspect
import json
import operator
import pickle
import random
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python
from unstd.containers import SortedDict, SortedList, SortedSet
from unstd.containers import sorted as sorted_


if TYPE_CHECKING:
    from pathlib import Path


requires_native = pytest.mark.skipif(
    not sorted_._HAVE_NATIVE, reason="native sorted kernels not built"
)


@requires_native
def test_native_types_inherit_upstream_without_modifying_its_methods() -> None:
    upstream = require_backend("sortedcontainers")
    from unstd.containers import _sorted_native

    for name in ("SortedList", "SortedDict", "SortedSet"):
        actual, original = getattr(sorted_, name), getattr(upstream, name)
        assert actual is not original
        assert issubclass(actual, original)
        assert inspect.signature(actual) == inspect.signature(original)
    assert inspect.isfunction(upstream.SortedList.add)
    assert inspect.isfunction(upstream.SortedList._loc)
    assert inspect.ismethoddescriptor(SortedList._loc)
    assert _sorted_native.KERNEL_VERSION == 3
    assert isinstance(SortedDict()._list, SortedList)
    assert isinstance(SortedSet()._list, SortedList)


@requires_native
@pytest.mark.parametrize("key", [None, operator.neg])
def test_sorted_list_splits_merges_and_ranks_match_upstream(key) -> None:
    upstream = require_backend("sortedcontainers")
    actual = SortedList(key=key)
    reference = upstream.SortedList(key=key)
    actual._reset(8)
    reference._reset(8)
    values = list(range(200)) * 2
    random.Random(813).shuffle(values)  # noqa: S311 — reproducible operation history.
    for index, value in enumerate(values):
        actual.add(value=value)
        reference.add(value)
        assert list(actual) == list(reference)
        for probe in (-1, value, 201):
            assert actual.bisect_left(value=probe) == reference.bisect_left(probe)
            assert actual.bisect_right(value=probe) == reference.bisect_right(probe)
            assert actual.bisect(value=probe) == reference.bisect(probe)
        assert actual[index // 2] == reference[index // 2]
        actual._check()
    while reference:
        index = (len(reference) * 7) // 13
        assert actual.pop(index) == reference.pop(index)
        assert list(actual) == list(reference)
        assert actual.bisect_left(100) == reference.bisect_left(100)
        actual._check()


@requires_native
@pytest.mark.parametrize("key", [None, operator.neg])
def test_keyed_factory_and_roundtrips_keep_types_and_order(key) -> None:
    upstream = require_backend("sortedcontainers")
    actual = SortedList([5, 1, 3, 1], key=key)
    assert isinstance(actual, SortedList)
    if key is not None:
        assert isinstance(actual, upstream.SortedKeyList)
    collections = (
        actual,
        SortedSet([5, 1, 3, 1], key=key),
        SortedDict(key, {5: "five", 1: "one", 3: "three"}),
    )
    for original in collections:
        for copied in (
            original.copy(),
            pickle.loads(pickle.dumps(original)),  # noqa: S301 — fixture-owned bytes.
        ):
            assert type(copied) is type(original)
            assert list(copied) == list(original)
            assert copied.key is original.key
        if hasattr(original, "_list"):
            assert isinstance(original._list, SortedList)
    assert isinstance(collections[1] | {7, 8}, SortedSet)
    assert isinstance(collections[1] & {1, 3}, SortedSet)


@requires_native
def test_subclass_overrides_keep_python_dispatch_and_factory_errors() -> None:
    class Derived(SortedList):
        def _expand(self, pos):
            self.expanded = True
            super()._expand(pos)

    values = Derived([1, 3])
    values.add(2)
    assert values.expanded
    assert list(values) == [1, 2, 3]
    with pytest.raises(TypeError, match="inherit SortedKeyList for key argument"):
        Derived(key=operator.neg)


@requires_native
def test_faulting_comparisons_preserve_exception_identity_and_state() -> None:
    error = LookupError("comparison refused")

    class Refused:
        def __lt__(self, other):
            raise error

        def __gt__(self, other):
            raise error

    values = SortedList([1, 3, 5])
    for operation in (values.add, values.bisect_left, values.bisect_right):
        with pytest.raises(LookupError) as caught:
            operation(Refused())
        assert caught.value is error
        assert list(values) == [1, 3, 5]
    values.add(4)
    assert list(values) == [1, 3, 4, 5]


_REENTRANT_COMPARISON = """
import json, sys
import sortedcontainers
from unstd.containers import SortedList

def run(ctor):
    trace = []
    armed = False
    collection = None

    def effect():
        nonlocal armed
        if armed:
            armed = False
            trace.append("mutate")
            if sys.argv[3] == "clear":
                collection.clear()
            else:
                replacement = collection.__dict__.copy()
                replacement.pop("_payload", None)
                collection.__dict__ = replacement

    class Result:
        def __init__(self, value): self.value = value
        def __bool__(self):
            trace.append("bool")
            effect()
            return self.value

    class Item:
        def __init__(self, value): self.value = value
        def __le__(self, other): return self.value <= other.value
        def __lt__(self, other):
            trace.append(["lt", self.value, other.value])
            answer = self.value < other.value
            if sys.argv[1] == "bool": return Result(answer)
            effect()
            return answer

    collection = ctor([Item(1), Item(3), Item(5)])
    if sys.argv[3] == "finalizer-dict":
        class Payload:
            def __del__(self):
                trace.append("finalize")
                collection.__dict__ = collection.__dict__.copy()
        collection._payload = Payload()
    trace.clear()
    armed = True
    error = None
    result = None
    try:
        result = getattr(collection, sys.argv[2])(Item(4))
    except Exception as caught:
        error = [type(caught).__name__, str(caught)]
    collection._check()
    return dict(trace=trace, error=error, result=result,
        values=[item.value for item in collection], length=len(collection))

print(json.dumps([run(sortedcontainers.SortedList), run(SortedList)]))
"""


@requires_native
@pytest.mark.parametrize("comparison", ["lt", "bool"])
@pytest.mark.parametrize("operation", ["add", "bisect_left", "bisect_right"])
def test_reentrant_comparison_clear_matches_upstream_in_owned_worker(
    tmp_path: Path, comparison: str, operation: str
) -> None:
    result = run_python(
        _REENTRANT_COMPARISON,
        [comparison, operation, "clear"],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


@requires_native
@pytest.mark.parametrize("comparison", ["lt", "bool"])
@pytest.mark.parametrize("operation", ["add", "bisect_left", "bisect_right"])
def test_replaced_instance_dictionary_preserves_successful_insert(
    tmp_path: Path, comparison: str, operation: str
) -> None:
    result = run_python(
        _REENTRANT_COMPARISON,
        [comparison, operation, "copy-dict"],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    if operation == "add":
        assert current["values"] == [1, 3, 4, 5]
        assert current["length"] == 4
    else:
        assert current["values"] == [1, 3, 5]
        assert current["length"] == 3
        assert current["result"] == 2


@requires_native
def test_integer_comparison_replaces_instance_dictionary_before_insert() -> None:
    target = SortedList([1, 3])
    armed = True

    class Probe(int):
        def __lt__(self, other):
            nonlocal armed
            if armed:
                armed = False
                target.__dict__ = target.__dict__.copy()
            return super().__lt__(other)

    target.add(Probe(2))
    assert list(target) == [1, 2, 3]
    assert len(target) == 3
    target._check()


@requires_native
def test_released_dictionary_finalizer_can_replace_dictionary_again(
    tmp_path: Path,
) -> None:
    result = run_python(
        _REENTRANT_COMPARISON,
        ["lt", "add", "finalizer-dict"],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert before.pop("trace").count("finalize") == 1
    assert current.pop("trace").count("finalize") == 1
    assert current == before
    assert current["values"] == [1, 3, 4, 5]
    assert current["length"] == 4


@requires_native
@pytest.mark.parametrize("position", [0, 5, -1, -7, 10**100, -(10**100)])
def test_integer_subclass_index_callbacks_match_upstream(position: int) -> None:
    backend = require_backend("sortedcontainers")

    def observe(ctor):
        trace = []

        class Index(int):
            def __lt__(self, other):
                trace.append(("lt", int(self), other))
                return super().__lt__(other)

            def __ge__(self, other):
                trace.append(("ge", int(self), other))
                return super().__ge__(other)

            def __add__(self, other):
                trace.append(("add", int(self), other))
                return super().__add__(other)

            def __sub__(self, other):
                trace.append(("sub", int(self), other))
                return super().__sub__(other)

            def __index__(self):
                trace.append(("index", int(self)))
                return super().__index__()

        collection = ctor(range(20))
        collection._reset(2)
        try:
            result = collection[Index(position)]
        except IndexError as error:
            result = (type(error).__name__, str(error))
        return result, trace

    assert observe(SortedList) == observe(backend.SortedList)


@requires_native
def test_exact_class_monkeypatch_of_positional_helper_remains_observable(
    monkeypatch,
) -> None:
    values = SortedList(range(40))
    values._reset(4)
    original = SortedList._loc
    calls = []

    def locate(self, position, index):
        calls.append((position, index))
        return original(self, position, index)

    monkeypatch.setattr(SortedList, "_loc", locate)
    assert values.bisect_left(19) == 19
    assert len(calls) == 1
    assert calls[0][0] > 0


@requires_native
def test_exact_class_monkeypatch_of_expansion_remains_observable(monkeypatch) -> None:
    values = SortedList([1, 3])
    original = SortedList._expand
    calls = []

    def expand(self, position):
        calls.append(position)
        return original(self, position)

    monkeypatch.setattr(SortedList, "_expand", expand)
    values.add(2)
    assert list(values) == [1, 2, 3]
    assert calls == [0]


@requires_native
@pytest.mark.parametrize("operation", ["rank", "index"])
@pytest.mark.parametrize("replace_index", [False, True])
def test_index_builder_dictionary_replacement_matches_upstream(
    monkeypatch, operation, replace_index
) -> None:
    backend = require_backend("sortedcontainers")

    def observe(ctor):
        values = ctor(range(40))
        values._reset(4)
        original = ctor._build_index
        calls = []

        def build(self):
            calls.append(True)
            self.__dict__ = self.__dict__.copy()
            if replace_index:
                self._index = []
            return original(self)

        with monkeypatch.context() as patch:
            patch.setattr(ctor, "_build_index", build)
            try:
                result = values.bisect_left(19) if operation == "rank" else values[19]
            except Exception as error:
                result = (type(error).__name__, str(error))
        values._check()
        return result, calls

    expected = observe(backend.SortedList)
    assert observe(SortedList) == expected
    assert expected[1] == [True]
    if not replace_index:
        assert expected[0] == 19


@requires_native
def test_native_pickles_load_with_the_original_backend_only(tmp_path: Path) -> None:
    fixture = tmp_path / "sorted.pickle"
    fixture.write_bytes(
        pickle.dumps(
            (
                SortedList([3, 1, 2]),
                SortedList([3, 1, 2], key=operator.neg),
                SortedSet([3, 1, 2]),
                SortedDict({3: "c", 1: "a", 2: "b"}),
            )
        )
    )
    result = run_python(
        """
import sys, pickle
from pathlib import Path
sys.modules["unstd.containers._sorted_native"] = None
import sortedcontainers as backend
values = pickle.loads(Path(sys.argv[1]).read_bytes())
expected = (backend.SortedList, backend.SortedKeyList,
    backend.SortedSet, backend.SortedDict)
for value, cls in zip(values, expected, strict=True):
    assert type(value) is cls
assert [list(value) for value in values] == [[1, 2, 3], [3, 2, 1], [1, 2, 3], [1, 2, 3]]
""",
        [str(fixture)],
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_missing_native_module_keeps_verbatim_upstream_types(tmp_path: Path) -> None:
    require_backend("sortedcontainers")
    result = run_python(
        """
import sys
sys.modules["unstd.containers._sorted_native"] = None
import sortedcontainers
from unstd.containers import SortedDict, SortedList, SortedSet
from unstd.containers import sorted as sorted_
assert not sorted_._HAVE_NATIVE
assert SortedList is sortedcontainers.SortedList
assert SortedDict is sortedcontainers.SortedDict
assert SortedSet is sortedcontainers.SortedSet
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_unverified_backend_version_keeps_its_own_classes(tmp_path: Path) -> None:
    require_backend("sortedcontainers")
    result = run_python(
        """
import sys, sortedcontainers
sortedcontainers.__version__ = "99.0.0"
from unstd.containers import SortedDict, SortedList, SortedSet
from unstd.containers import sorted as sorted_
assert not sorted_._HAVE_NATIVE
assert "unstd.containers._sorted_native" not in sys.modules
assert SortedList is sortedcontainers.SortedList
assert SortedDict is sortedcontainers.SortedDict
assert SortedSet is sortedcontainers.SortedSet
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
