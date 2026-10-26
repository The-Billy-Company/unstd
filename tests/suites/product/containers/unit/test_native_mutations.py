"""Numeric mutation and bulk deletion preserve identities and callbacks.

Bulk integer fast paths are checked separately from callback-bearing fallbacks.
Reentrant cases run in owned interpreters, including finalizers which
observe upstream's deliberately partial mutation states.
"""

from __future__ import annotations

import bisect
import cProfile
import inspect
import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python
from unstd.containers import SortedList
from unstd.containers import sorted as sorted_


if TYPE_CHECKING:
    from pathlib import Path


_REQUIRE_TEST_HOOKS = os.environ.get("UNSTD_CONTAINERS_REQUIRE_TEST_HOOKS") == "1"


def _supports_test_hooks():
    try:
        from unstd.containers import _sorted_native
    except ImportError:
        return False
    return hasattr(_sorted_native, "_set_test_hook")


requires_hooks = pytest.mark.skipif(
    not (_REQUIRE_TEST_HOOKS or _supports_test_hooks()),
    reason="mutation test hooks are absent from production builds",
)
pytestmark = pytest.mark.skipif(
    not sorted_._HAVE_NATIVE and not _REQUIRE_TEST_HOOKS,
    reason="native sorted kernels not built",
)


def _same_objects(actual, expected):
    observed = list(actual)
    assert len(observed) == len(expected)
    assert all(left is right for left, right in zip(observed, expected, strict=True))


def _profiled(call, *args):
    profile = cProfile.Profile()
    result = profile.runcall(call, *args)
    return result, {
        entry.code for entry in profile.getstats() if not isinstance(entry.code, str)
    }


def test_bulk_deletion_installs_only_on_owned_class_and_accepts_integer_ranges():
    backend = require_backend("sortedcontainers")
    from unstd.containers import _sorted_native

    assert inspect.ismethoddescriptor(SortedList.__delitem__)
    assert inspect.isfunction(backend.SortedList.__delitem__)
    for selection, expected in (
        (slice(None, 1000), list(range(1000, 10000))),
        (slice(9000, None), list(range(9000))),
    ):
        values = SortedList(range(10000))
        values._reset(64)
        values.bisect_left(4999)
        before = _sorted_native.mutation_stats(SortedList)["delete_fast"]
        result = values.__delitem__(selection)
        assert result is None
        assert _sorted_native.mutation_stats(SortedList)["delete_fast"] > before
        assert list(values) == expected
        values._check()


@pytest.mark.parametrize("payload_type", [list, tuple])
def test_monotonic_batch_update_accepts_bounded_integer_tail(payload_type):
    backend = require_backend("sortedcontainers")
    from unstd.containers import _sorted_native

    assert inspect.ismethoddescriptor(SortedList.update)
    assert inspect.isfunction(backend.SortedList.update)
    values = SortedList(range(10000))
    values._reset(64)
    values.bisect_left(4999)
    payload = payload_type(range(10000, 11000))
    before = _sorted_native.mutation_stats(SortedList)["update_fast"]
    result = values.update(payload)
    assert result is None
    assert _sorted_native.mutation_stats(SortedList)["update_fast"] > before
    assert list(values) == list(range(11000))
    values._check()


def test_duplicate_integer_object_identity_and_pop_return_are_preserved():
    original = [int("1000") for _ in range(32)]
    values = SortedList(original)
    values._reset(8)
    model = original.copy()
    extra = int("1000")
    assert values.add(value=extra) is None
    bisect.insort(model, extra)
    _same_objects(values, model)
    assert values.remove(value=int("1000")) is None
    model.pop(0)
    _same_objects(values, model)
    assert values.discard(value=int("1000")) is None
    model.pop(0)
    _same_objects(values, model)
    assert values.pop() is model.pop()
    del values[3]
    del model[3]
    _same_objects(values, model)
    values._check()


class Integer(int):
    """An int subtype which must retain the ordinary upstream route."""


@pytest.mark.parametrize(
    "probe",
    [
        -(1 << 63) - 1,
        -(1 << 63),
        -(1 << 63) + 1,
        (1 << 63) - 2,
        (1 << 63) - 1,
        1 << 63,
        False,
        True,
        Integer(17),
    ],
)
def test_integer_boundaries_bool_and_subtypes_keep_exact_object_semantics(probe):
    initial = [-(1 << 63) + 2, -17, 0, 17, (1 << 63) - 3]
    values = SortedList(initial)
    values._reset(4)
    model = initial.copy()
    assert values.add(value=probe) is None
    bisect.insort(model, probe)
    _same_objects(values, model)
    assert values.remove(value=probe) is None
    model.remove(probe)
    _same_objects(values, model)
    assert values.discard(value=probe) is None
    if probe in model:
        model.remove(probe)
    _same_objects(values, model)
    values._check()


def test_split_merge_and_empty_transitions_delegate_before_mutation():
    backend = require_backend("sortedcontainers")
    values = SortedList(range(16))
    values._reset(8)
    for _ in range(8):
        values.add(0)
    assert len(values._lists[0]) == 16
    model = list(values)
    _, calls = _profiled(values.add, 0)
    assert backend.SortedList.add.__code__ in calls
    bisect.insort(model, 0)
    assert list(values) == model
    values._check()

    values = SortedList(range(16))
    values._reset(8)
    for value in range(3):
        values.remove(value)
    _, calls = _profiled(values.remove, 3)
    assert backend.SortedList.remove.__code__ in calls
    assert list(values) == list(range(4, 16))
    values._check()
    emptying = SortedList([7])
    _, calls = _profiled(emptying.remove, 7)
    assert backend.SortedList.remove.__code__ in calls
    assert list(emptying) == []
    emptying._check()


@pytest.mark.parametrize("placement", ["instance", "class"])
def test_delete_helper_override_is_observed_before_any_native_effect(
    monkeypatch, placement
):
    values = SortedList(range(64))
    values._reset(8)
    values.bisect_left(31)
    before_index = values._index.copy()
    calls = []
    if placement == "instance":
        values._delete = lambda pos, idx: calls.append((pos, idx))
    else:

        def delete(self, pos, idx):
            calls.append((pos, idx))

        monkeypatch.setattr(SortedList, "_delete", delete)
    assert values.remove(20) is None
    assert calls == [(2, 4)]
    assert list(values) == list(range(64))
    assert values._index == before_index
    values._check()


@pytest.mark.parametrize("placement", ["instance", "class"])
def test_expansion_override_keeps_upstream_cache_side_effect_order(
    monkeypatch, placement
):
    values = SortedList(range(64))
    values._reset(8)
    values.bisect_left(31)
    before_index = values._index.copy()
    calls = []
    if placement == "instance":
        values._expand = lambda pos: calls.append(pos)
    else:

        def expand(self, pos):
            calls.append(pos)

        monkeypatch.setattr(SortedList, "_expand", expand)
    assert values.add(20) is None
    assert calls == [2]
    assert list(values) == sorted([*range(64), 20])
    assert len(values) == 65
    assert values._index == before_index
    values._index.clear()
    values._check()


_EQUALITY = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    armed = False
    collection = None
    error = MemoryError("comparison allocation refused")
    mode = sys.argv[2]

    class Truth:
        def __bool__(self):
            trace.append("bool")
            if mode == "error": raise error
            if mode == "clear": collection.clear()
            if mode == "copy-dict": collection.__dict__ = collection.__dict__.copy()
            return mode not in ("false", "reflected-false")

    class Stored(int):
        def __lt__(self, other):
            if armed: trace.append(["stored-lt", int(self), int(other)])
            return super().__lt__(other)
        def __eq__(self, other):
            if not armed: return super().__eq__(other)
            trace.append("stored-eq")
            return NotImplemented if mode.startswith("reflected") else Truth()

    class Probe(int):
        def __eq__(self, other):
            trace.append("probe-eq")
            return Truth()
        def __repr__(self):
            trace.append("repr")
            return str(int(self))

    collection = ctor([1000, Stored(1001), 1002])
    trace.clear()
    armed = True
    probe = Probe(1001) if mode.startswith("reflected") else int("1001")
    caught = None
    result = None
    try: result = getattr(collection, sys.argv[1])(probe)
    except Exception as problem:
        caught = [type(problem).__name__, str(problem), problem is error]
    armed = False
    collection._check()
    return dict(trace=trace, error=caught, result=result,
        values=[int(item) for item in collection], length=len(collection))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("operation", ["remove", "discard"])
@pytest.mark.parametrize(
    "mode",
    ["true", "false", "error", "clear", "copy-dict", "reflected", "reflected-false"],
)
def test_equality_reflection_truth_and_reentry_match_upstream(
    tmp_path: Path, operation, mode
):
    result = run_python(_EQUALITY, [operation, mode], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


_FINALIZERS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    collection = None
    class Victim(int):
        def __del__(self):
            trace.append(["finalize", len(collection), [int(item) for item in collection]])
    collection = ctor([1000, Victim(1001), 1002])
    mode = sys.argv[1]
    if mode in ("remove", "discard"): getattr(collection, mode)(int("1001"))
    elif mode == "delete": del collection[1]
    else:
        result = collection.pop(1)
        assert isinstance(result, Victim)
        assert trace == []
        del result
    collection._check()
    return dict(trace=trace, values=list(collection), length=len(collection))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("operation", ["remove", "discard", "delete", "pop"])
def test_custom_victim_finalizer_observes_upstream_partial_state(
    tmp_path: Path, operation
):
    result = run_python(_FINALIZERS, [operation], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"] == [
        ["finalize", 2 if operation == "pop" else 3, [1000, 1002]]
    ]


_HELPER_DICTIONARY = """
import json, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    collection = ctor([1, 3])
    class Payload:
        def __del__(self):
            trace.append(["finalize", len(collection), list(collection)])
    collection._payload = Payload()
    def expand(position):
        trace.append(["expand", position])
        replacement = collection.__dict__.copy()
        replacement.pop("_payload")
        collection.__dict__ = replacement
        trace.append(["after-dict", len(collection), list(collection)])
    collection._expand = expand
    collection.add(2)
    collection._check()
    return dict(trace=trace, length=len(collection), values=list(collection))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


def test_fallback_does_not_retain_detached_dictionary_finalizers(tmp_path: Path):
    result = run_python(_HELPER_DICTIONARY, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"] == [
        ["expand", 0],
        ["finalize", 2, [1, 2, 3]],
        ["after-dict", 2, [1, 2, 3]],
    ]


_MALFORMED_FIELDS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    error = MemoryError("metadata allocation refused")
    class Field(int):
        def fail(self, name, other):
            trace.append([name, int(self), other])
            raise error
        def __add__(self, other): return self.fail("add", other)
        def __sub__(self, other): return self.fail("sub", other)
        def __lshift__(self, other): return self.fail("left", other)
        def __rshift__(self, other): return self.fail("right", other)
    collection = ctor(range(64))
    collection._reset(8)
    collection.bisect_left(31)
    attribute = sys.argv[2]
    if attribute == "index":
        position = collection._offset + 2
        collection._index[position] = Field(collection._index[position])
    else:
        setattr(collection, f"_{attribute}", Field(getattr(collection, f"_{attribute}")))
    caught = None
    try: getattr(collection, sys.argv[1])(20)
    except Exception as problem:
        caught = [type(problem).__name__, str(problem), problem is error]
    return dict(trace=trace, error=caught, values=list(collection),
        length=int(collection._len), index=[int(item) for item in collection._index],
        maxes=list(collection._maxes), blocks=[list(block) for block in collection._lists])

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("operation", ["add", "remove", "discard"])
@pytest.mark.parametrize("attribute", ["index", "offset", "len", "load"])
def test_unsupported_metadata_replays_original_memoryerror_order(
    tmp_path: Path, operation, attribute
):
    result = run_python(
        _MALFORMED_FIELDS, [operation, attribute], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["error"] == ["MemoryError", "metadata allocation refused", True]


@pytest.mark.parametrize("operation", ["add", "remove", "discard"])
@pytest.mark.parametrize("refused", [False, True])
def test_keyed_ties_and_key_memoryerror_keep_callback_order(operation, refused):
    backend = require_backend("sortedcontainers")

    def observe(ctor):
        trace = []
        armed = False
        error = MemoryError("key allocation refused")

        def key(item):
            if armed:
                trace.append(item[0])
                if refused:
                    raise error
            return item[1]

        collection = ctor([("a", 0), ("b", 0), ("c", 1)], key=key)
        assert inspect.isfunction(type(collection).add)
        armed = True
        probe = ("boom", 0) if refused else ("d", 0) if operation == "add" else ("b", 0)
        caught = None
        try:
            result = getattr(collection, operation)(probe)
        except MemoryError as problem:
            caught = problem is error
            result = None
        armed = False
        collection._check()
        return list(collection), trace, caught, result

    assert observe(SortedList) == observe(backend.SortedList)


_SORTED_DICT = """
import json, sortedcontainers
from unstd.containers import SortedDict

def observe(ctor):
    target = None
    trace = []
    def snapshot(stage):
        trace.append([stage, dict.__len__(target), len(target._list),
            dict.__contains__(target, 20), 20 in target._list,
            dict.__contains__(target, 99), 99 in target._list])
    class Value:
        def __del__(self):
            snapshot("before-reentry")
            target[99] = "reentrant"
            snapshot("after-reentry")
    target = ctor((key, str(key)) for key in range(64))
    target._reset(8)
    target.bisect_left(31)
    target[20] = Value()
    del target[20]
    target._check()
    return dict(trace=trace, keys=list(target), values=dict(target))

print(json.dumps([observe(sortedcontainers.SortedDict), observe(SortedDict)]))
"""


def test_sorteddict_value_finalizer_reenters_before_ordered_key_deletion(
    tmp_path: Path,
):
    result = run_python(_SORTED_DICT, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"] == [
        ["before-reentry", 63, 64, False, True, False, False],
        ["after-reentry", 64, 65, False, True, True, True],
    ]


_SORTED_SET = """
import json, sys, sortedcontainers
from unstd.containers import SortedSet

def observe(ctor):
    target = None
    trace = []
    armed = False
    fired = False
    hashes = 0
    error = MemoryError("hash allocation refused")
    operation, mode = sys.argv[1:]
    class Probe(int):
        def __hash__(self):
            nonlocal fired, hashes
            if armed:
                hashes += 1
                trace.append(["hash", hashes, len(target), len(target._list)])
                if (mode == "first" and hashes == 1) or (mode == "second" and hashes == 2):
                    raise error
                if mode == "reenter" and not fired:
                    fired = True
                    target.add(99)
            return super().__hash__()
        def __lt__(self, other):
            if armed: trace.append(["lt", int(self), int(other)])
            return super().__lt__(other)
    probe = Probe(65 if operation == "add" else 20)
    initial = list(range(64)) if operation == "add" else [value for value in range(64) if value != 20] + [probe]
    target = ctor(initial)
    target._reset(8)
    target.bisect_left(31)
    armed = True
    caught = None
    try: getattr(target, operation)(probe)
    except MemoryError as problem:
        caught = [str(problem), problem is error]
    armed = False
    target._check()
    return dict(trace=trace, error=caught, values=[int(value) for value in target])

print(json.dumps([observe(sortedcontainers.SortedSet), observe(SortedSet)]))
"""


@pytest.mark.parametrize("operation", ["add", "remove", "discard"])
@pytest.mark.parametrize("mode", ["first", "second", "reenter"])
def test_sortedset_hash_failures_and_reentry_keep_outer_mutation_order(
    tmp_path: Path, operation, mode
):
    result = run_python(_SORTED_SET, [operation, mode], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


_BULK_PARITY = """
import bisect, json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    values = ctor(range(512))
    values._reset(8)
    values.bisect_left(255)
    selection = json.loads(sys.argv[1])
    if isinstance(selection, list): selection = slice(*selection)
    error = None
    try: result = values.__delitem__(index=selection)
    except Exception as problem:
        error = [type(problem).__name__, str(problem)]
        result = None
    values._check()
    observed = list(values)
    ranks = [(values.bisect_left(probe), values.bisect_right(probe))
        for probe in (-1, 0, 127, 255, 511, 512)]
    assert ranks == [(bisect.bisect_left(observed, probe), bisect.bisect_right(observed, probe))
        for probe in (-1, 0, 127, 255, 511, 512)]
    assert list(reversed(values)) == observed[::-1]
    if observed:
        assert values[len(observed) // 2] == observed[len(observed) // 2]
    return dict(error=error, result=result, values=observed, length=len(values), ranks=ranks)

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize(
    "selection",
    [
        [None, None, None],
        [None, 0, None],
        [0, 1, None],
        [0, 7, None],
        [0, 8, None],
        [0, 9, None],
        [0, 63, None],
        [0, 64, None],
        [0, 65, None],
        [0, 128, None],
        [0, 129, None],
        [-1, None, None],
        [504, None, None],
        [503, None, None],
        [385, None, None],
        [-129, None, None],
        [None, -129, None],
        [-(10**100), 2, None],
        [510, 10**100, None],
        [20, 40, 1],
        [None, None, -1],
        [0, 100, 2],
        [None, None, 0],
        [None, 10, True],
        -1,
        17,
        512,
        10**100,
        True,
    ],
)
def test_bulk_and_fallback_slice_shapes_match_upstream(tmp_path: Path, selection):
    result = run_python(_BULK_PARITY, [json.dumps(selection)], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


_BULK_HELPERS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    helper, placement, mode = sys.argv[1:]
    values = ctor(range(512))
    values._reset(8)
    trace = []
    error = MemoryError("helper allocation refused")
    original = getattr(values, helper)
    missing = object()
    saved = ctor.__dict__.get(helper, missing)
    def normalize(value):
        if isinstance(value, slice): return ["slice", value.start, value.stop, value.step]
        if isinstance(value, list): return ["list", len(value), value[:2], value[-2:]]
        return value
    def hook(*args):
        if placement == "class": args = args[1:]
        trace.append([normalize(arg) for arg in args])
        if mode == "raise": raise error
        result = original(*args)
        return "marker" if helper in ("_clear", "_update") else result
    if placement == "class": setattr(ctor, helper, hook)
    else: setattr(values, helper, hook)
    selection = slice(None) if helper == "_clear" else slice(None, 129)
    if helper in ("_pos", "_delete", "_expand"): selection = slice(None, 5)
    if helper == "_build_index": selection = slice(-5, None)
    caught = None
    result = None
    try: result = values.__delitem__(index=selection)
    except Exception as problem:
        caught = [type(problem).__name__, str(problem), problem is error]
    finally:
        if placement == "class":
            if saved is missing: delattr(ctor, helper)
            else: setattr(ctor, helper, saved)
    values._check()
    return dict(trace=trace, error=caught, result=result, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("placement", ["class", "instance"])
@pytest.mark.parametrize(
    ("helper", "mode"),
    [
        ("_clear", "call"),
        ("_update", "call"),
        ("_getitem", "call"),
        ("_pos", "call"),
        ("_delete", "call"),
        ("_expand", "call"),
        ("_build_index", "call"),
        ("_clear", "raise"),
        ("_update", "raise"),
    ],
)
def test_bulk_deletion_preserves_private_helper_dispatch_and_results(
    tmp_path: Path, placement, helper, mode
):
    result = run_python(
        _BULK_HELPERS, [helper, placement, mode], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"]


_SLICE_CALLBACKS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    values = ctor(range(512))
    trace = []
    error = MemoryError("slice allocation refused")
    fired = False
    class Bound:
        def __init__(self, value, name): self.value, self.name = value, name
        def __index__(self):
            nonlocal fired
            trace.append(self.name)
            if not fired:
                fired = True
                if sys.argv[1] == "raise": raise error
                if sys.argv[1] == "clear": values.clear()
                if sys.argv[1] == "dict": values.__dict__ = values.__dict__.copy()
            return self.value
    selection = slice(Bound(0, "start"), Bound(129, "stop"), Bound(1, "step"))
    caught = None
    try: values.__delitem__(index=selection)
    except Exception as problem:
        caught = [type(problem).__name__, str(problem), problem is error]
    values._check()
    return dict(trace=trace, error=caught, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("mode", ["call", "raise", "clear", "dict"])
def test_slice_index_callbacks_are_not_skipped_or_replayed(tmp_path: Path, mode):
    result = run_python(_SLICE_CALLBACKS, [mode], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


_KEYWORD_SUBTYPE = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    values = ctor(range(10000)) if sys.argv[1] == "update" else ctor(range(128))
    trace = []
    class Name(str):
        def __eq__(self, other):
            trace.append(str(other))
            return super().__eq__(other)
        __hash__ = str.__hash__
    operation = sys.argv[1]
    argument = slice(None, 33) if operation == "__delitem__" else tuple(range(10000, 11000)) if operation == "update" else 20
    name = "index" if operation == "__delitem__" else "iterable" if operation == "update" else "value"
    result = getattr(values, operation)(**{Name(name): argument})
    values._check()
    return dict(trace=trace, result=result, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize(
    "operation", ["add", "remove", "discard", "bisect_left", "__delitem__", "update"]
)
def test_string_subclass_keywords_retain_python_binding_callbacks(
    tmp_path: Path, operation
):
    result = run_python(_KEYWORD_SUBTYPE, [operation], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"]


_BULK_ALIASES = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    kind, side, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    values = ctor(range(512))
    values._reset(8)
    values.bisect_left(255)
    selection = slice(None, count) if side == "prefix" else slice(-count, None)
    if kind == "iterator":
        alias = iter(values)
        first = [next(alias) for _ in range(3)]
    elif kind == "range":
        alias = values.irange(0, 511)
        first = [next(alias) for _ in range(3)]
    elif kind == "dict": alias = values.__dict__
    elif kind == "index": alias = values._index
    elif kind == "outer": alias = values._lists
    else:
        boundary = count if side == "prefix" else 512 - count - 1
        alias = values._lists[boundary // 8]
    values.__delitem__(index=selection)
    values._check()
    if kind in ("iterator", "range"):
        try: observed = [first, list(alias)]
        except Exception as error: observed = [type(error).__name__, str(error)]
    elif kind == "dict":
        observed = [alias["_len"], [list(block) for block in alias["_lists"]], list(alias["_index"])]
    elif kind == "outer": observed = [list(block) for block in alias]
    else: observed = list(alias)
    return dict(alias=observed, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize(
    "kind", ["iterator", "range", "dict", "index", "outer", "boundary-block"]
)
@pytest.mark.parametrize("side", ["prefix", "suffix"])
@pytest.mark.parametrize("count", [53, 129])
def test_active_iterators_and_storage_aliases_keep_upstream_behavior(
    tmp_path: Path, kind, side, count
):
    result = run_python(
        _BULK_ALIASES, [kind, side, str(count)], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


_BULK_FINALIZERS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    count, side = int(sys.argv[1]), sys.argv[2]
    values = None
    trace = []
    class Victim(int):
        def __del__(self):
            trace.append([len(values), list(values), len(values._maxes),
                list(values._index), values._offset])
    victim = 1 if side == "prefix" else 126
    initial = [Victim(value) if value == victim else value for value in range(128)]
    values = ctor(initial)
    del initial
    values._reset(8)
    values.bisect_left(63)
    selection = slice(None, count) if side == "prefix" else slice(-count, None)
    values.__delitem__(index=selection)
    values._check()
    return dict(trace=trace, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("count", [3, 53])
@pytest.mark.parametrize("side", ["prefix", "suffix"])
def test_removed_custom_values_finalize_at_original_bulk_fallback_stage(
    tmp_path: Path, count, side
):
    result = run_python(_BULK_FINALIZERS, [str(count), side], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert len(current["trace"]) == 1


_RETAINED_FINALIZER = """
import json, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    values = None
    trace = []
    class Last(int):
        def __del__(self):
            trace.append([len(values), list(values), list(values._index), values._offset])
    values = ctor([*range(511), Last(511)])
    values._reset(8)
    values.bisect_left(255)
    values.__delitem__(index=slice(None, 129))
    assert trace == []
    assert isinstance(values[-1], Last)
    values.bisect_left(300)
    values.__delitem__(index=slice(None))
    values._check()
    return dict(trace=trace, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


def test_retained_custom_last_value_is_not_finalized_during_prefix_deletion(
    tmp_path: Path,
):
    result = run_python(_RETAINED_FINALIZER, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert len(current["trace"]) == 1


_STATS_SAFETY = """
import datetime, json, sortedcontainers
from unstd.containers import SortedList, _sorted_native

invalid = [object, int, list, type, sortedcontainers.SortedList,
    None, 0, object(), [], {}, "SortedList"]
class Unconfigured(SortedList): pass
invalid.append(Unconfigured)
for argument in invalid:
    try: _sorted_native.mutation_stats(argument)
    except TypeError: pass
    else: raise AssertionError(f"accepted invalid stats argument {argument!r}")

for mutation in ("missing", "object", "capsule"):
    class Owned(sortedcontainers.SortedList): pass
    _sorted_native.install(Owned, sortedcontainers.SortedList)
    report = _sorted_native.mutation_stats(Owned)
    assert isinstance(report, dict)
    if mutation == "missing": del Owned._unstd_sorted_native_config
    elif mutation == "object": Owned._unstd_sorted_native_config = object()
    else: Owned._unstd_sorted_native_config = datetime.datetime_CAPI
    try: _sorted_native.mutation_stats(Owned)
    except TypeError: pass
    else: raise AssertionError(f"accepted invalid config {mutation}")
print(json.dumps({"invalid": len(invalid), "configurations": 3}))
"""


def test_private_mutation_stats_rejects_invalid_types_and_capsules(tmp_path: Path):
    result = run_python(_STATS_SAFETY, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"invalid": 12, "configurations": 3}


_GC_REENTRY = """
import gc, json, sys, sortedcontainers
from unstd.containers import SortedList

mode = sys.argv[1]
expected = list(range(513, 4096))

def original_alias():
    reference = sortedcontainers.SortedList(range(4096))
    reference._reset(64)
    reference.bisect_left(2047)
    if mode in ("iterator", "range"):
        alias = iter(reference) if mode == "iterator" else reference.irange()
        first = next(alias)
    elif mode == "index": alias = reference._index
    reference.__delitem__(slice(None, 513))
    if mode in ("iterator", "range"):
        try: return "values", first, list(alias)
        except Exception as error: return "error", type(error).__name__, str(error)
    if mode == "index": return list(alias)
    return None

before_alias = original_alias()
values = SortedList(range(4096))
values._reset(64)
values.bisect_left(2047)
operation = values.__delitem__
selection = slice(None, 513)
observations, held, failures = [], [], []
previous_enabled, thresholds = gc.isenabled(), gc.get_threshold()
gc.disable()
gc.collect()

class Trigger:
    def __init__(self): self.cycle = self
    def __del__(self):
        try:
            observations.append((len(values), values[0]))
            if mode in ("iterator", "range"):
                alias = iter(values) if mode == "iterator" else values.irange()
                held.append((next(alias), alias))
            elif mode == "index": held.append(values._index)
            else:
                values.__dict__ = values.__dict__.copy()
                held.append(values.__dict__)
        except Exception as problem:
            failures.append([type(problem).__name__, str(problem)])

trigger = Trigger()
del trigger
gc.set_threshold(1, 1, 1)
gc.enable()
try:
    operation(selection)
finally:
    gc.disable()
gc.collect()
try:
    assert failures == [], failures
    assert len(observations) == 1, observations
    initial = observations[0] == (4096, 0)
    assert initial or observations[0] == (3583, 513), observations
    assert list(values) == expected
    assert len(values) == len(expected)
    values._check()
    if mode in ("iterator", "range"):
        first, alias = held[0]
        try: observed = "values", first, list(alias)
        except Exception as error: observed = "error", type(error).__name__, str(error)
        assert observed == (before_alias if initial else ("values", 513, expected[1:])), observed
    elif mode == "index":
        assert list(held[0]) == (before_alias if initial else list(values._index))
    else:
        assert held[0]["_len"] == len(expected)
        assert [item for block in held[0]["_lists"] for item in block] == expected
finally:
    gc.set_threshold(*thresholds)
    if previous_enabled: gc.enable()
print(json.dumps({"mode": mode, "phase": "before" if initial else "after", "length": len(values)}))
"""


@pytest.mark.parametrize("mode", ["iterator", "range", "dict", "index"])
def test_gc_created_alias_or_dictionary_swap_observes_coherent_bulk_state(
    tmp_path: Path, mode
):
    result = run_python(_GC_REENTRY, [mode], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == mode
    assert report["length"] == 3583
    assert report["phase"] in {"before", "after"}


_READ_FINALIZER_ORDER = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    armed = False
    values = None
    def detach():
        nonlocal armed
        if not armed: return
        armed = False
        trace.append("before")
        replacement = values.__dict__.copy()
        replacement.pop("_payload")
        values.__dict__ = replacement
        trace.append("after")
    class Payload:
        def __del__(self): trace.append("finalize")
    class Item(int):
        def __lt__(self, other):
            detach()
            return super().__lt__(other)
        def __gt__(self, other):
            detach()
            return super().__gt__(other)
    class Name(str):
        def __eq__(self, other):
            detach()
            return super().__eq__(other)
        __hash__ = str.__hash__
    mode, operation = sys.argv[1:]
    values = ctor([Item(value) if value == 64 else value for value in range(128)]) if mode == "stored" else ctor(range(64))
    values._reset(8)
    if mode == "builder":
        original = values._build_index
        def build():
            detach()
            return original()
        values._build_index = build
    values._payload = Payload()
    armed = True
    if mode == "keyword":
        name = "index" if operation == "__delitem__" else "value"
        argument = slice(None, 17) if operation == "__delitem__" else 20
        result = getattr(values, operation)(**{Name(name): argument})
    else:
        argument = Item(20) if mode == "comparison" else 64 if mode == "stored" else 20
        result = getattr(values, operation)(argument)
    values._check()
    return dict(trace=trace, result=result, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize(
    ("mode", "operation"),
    [
        ("comparison", "bisect_left"),
        ("comparison", "bisect_right"),
        ("stored", "bisect_left"),
        ("stored", "bisect_right"),
        ("builder", "bisect_left"),
        ("keyword", "bisect_left"),
        ("keyword", "__delitem__"),
    ],
)
def test_read_and_binding_callbacks_finalize_detached_payload_before_return(
    tmp_path: Path, mode, operation
):
    result = run_python(
        _READ_FINALIZER_ORDER, [mode, operation], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"] == ["before", "finalize", "after"]


@requires_hooks
def test_testing_build_exposes_required_mutation_hooks():
    from unstd.containers import _sorted_native

    assert callable(getattr(_sorted_native, "_set_test_hook", None))


_FAILPOINT_SWEEP = """
import json, sys
from unstd.containers import SortedList, _sorted_native as native

assert callable(getattr(native, "_set_test_hook", None)), "required mutation test hooks are missing"
mode = sys.argv[1]
payload = tuple(range(10000, 11000))

def create():
    values = SortedList(range(10000))
    values._reset(64)
    values.bisect_left(4999)
    return values

def snapshot(values):
    return (tuple(values), values._len, values._load, values._offset,
        tuple(tuple(block) for block in values._lists), tuple(values._maxes), tuple(values._index),
        tuple(id(values.__dict__[name]) for name in ("_lists", "_maxes", "_index")))

def apply(values):
    if mode == "append": return values.update(payload)
    selection = slice(None, 1000) if mode == "prefix" else slice(-1000, None)
    return values.__delitem__(selection)

values = create()
native._set_test_hook(SortedList)
try:
    apply(values)
finally:
    total = native._set_test_hook(SortedList)
assert total > 0, "the intended native mutator did not reach a test point"
values._check()
expected = list(range(11000)) if mode == "append" else list(range(1000, 10000)) if mode == "prefix" else list(range(9000))
assert list(values) == expected

for point in range(total):
    values = create()
    before = snapshot(values)
    native._set_test_hook(SortedList, fail_after=point)
    failed = False
    try:
        apply(values)
    except MemoryError:
        failed = True
    finally:
        observed = native._set_test_hook(SortedList)
    assert failed, (point, total, observed)
    assert snapshot(values) == before, ("rollback changed source", point)
    values._check()

values = create()
native._set_test_hook(SortedList, fail_after=total)
try:
    apply(values)
finally:
    native._set_test_hook(SortedList)
assert list(values) == expected
values._check()
print(json.dumps({"mode": mode, "failed_points": total, "success_after_last": True}))
"""


@requires_hooks
@pytest.mark.parametrize("mode", ["prefix", "suffix", "append"])
def test_every_native_staging_and_commit_failure_restores_source(tmp_path: Path, mode):
    result = run_python(_FAILPOINT_SWEEP, [mode], cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == mode
    assert report["failed_points"] > 0
    assert report["success_after_last"]


_STAGE_REENTRY = """
import cProfile, gc, json, sys, sortedcontainers
from unstd.containers import SortedList, _sorted_native as native

assert callable(getattr(native, "_set_test_hook", None)), "required mutation test hooks are missing"
mode, action = sys.argv[1:]
payload = list(range(10000, 11000)) if action.startswith("input") else tuple(range(10000, 11000))
selection = slice(None, 1000) if mode == "prefix" else slice(-1000, None)

def apply(values):
    return values.update(payload) if mode == "append" else values.__delitem__(selection)

def consume(alias):
    try: return "values", list(alias)
    except Exception as error: return "error", type(error).__name__, str(error)

def expected_alias():
    values = sortedcontainers.SortedList(range(10000))
    values._reset(64)
    values.bisect_left(4999)
    if action in ("iterator", "gc-iterator"):
        alias = iter(values)
        first = next(alias)
    elif action == "range":
        alias = values.irange()
        first = next(alias)
    elif action == "index": alias = values._index
    elif action == "fields":
        values._lists = values._lists.copy()
        alias = values._lists
    else: return None
    apply(values)
    if action in ("iterator", "gc-iterator", "range"): return first, consume(alias)
    if action == "index": return list(alias)
    return [list(block) for block in alias]

expected_aliased = expected_alias()
values = SortedList(range(10000))
values._reset(64)
values.bisect_left(4999)
held, hooks, finalized = [], [], []
helper_calls = 0
derived = None
helper = "_expand" if mode == "append" else "_delete"
original = getattr(SortedList, helper)
missing = object()
saved = SortedList.__dict__.get(helper, missing)
previous_gc = gc.isenabled()
gc.disable()

class Trigger:
    def __init__(self): self.cycle = self
    def __del__(self):
        alias = iter(values)
        held.append((next(alias), alias))
        finalized.append(True)

if action == "gc-iterator":
    trigger = Trigger()
    del trigger

def hook():
    global helper_calls, derived
    hooks.append((len(values), values[0], values[-1]))
    assert hooks == [(10000, 0, 9999)], hooks
    if action in ("iterator", "range"):
        alias = iter(values) if action == "iterator" else values.irange()
        held.append((next(alias), alias))
    elif action == "gc-iterator": gc.collect()
    elif action == "dict":
        # Keep the detached dictionary as an external storage alias. A copy
        # alone, with identical fields and no retained old owner, is coherent.
        detached = values.__dict__
        values.__dict__ = detached.copy()
        held.append(detached)
    elif action == "index": held.append(values._index)
    elif action == "reinstall": native.install(SortedList, sortedcontainers.SortedList)
    elif action == "fields":
        values._lists = values._lists.copy()
        held.append(values._lists)
    elif action == "clear": values.clear()
    elif action == "input": payload[500] = -1
    elif action == "input-identity": payload[500] = int(str(payload[500]))
    else:
        def changed(self, *args):
            global helper_calls
            helper_calls += 1
            return original(self, *args)
        if action == "subtype":
            class Derived(SortedList): pass
            setattr(Derived, helper, changed)
            derived = Derived
            values.__class__ = Derived
        else: setattr(SortedList, helper, changed)

native._set_test_hook(SortedList, callback=hook)
profile = cProfile.Profile()
try:
    profile.runcall(apply, values)
finally:
    native._set_test_hook(SortedList)
    if saved is missing:
        if helper in SortedList.__dict__: delattr(SortedList, helper)
    else: setattr(SortedList, helper, saved)
    if previous_gc: gc.enable()
assert len(hooks) == 1, hooks
calls = {entry.code for entry in profile.getstats() if not isinstance(entry.code, str)}
upstream = sortedcontainers.SortedList.update if mode == "append" else sortedcontainers.SortedList.__delitem__
assert upstream.__code__ in calls, "post-staging mutation did not fall back to upstream"
stats = native.mutation_stats(SortedList)
assert stats["delete_fast"] == stats["update_fast"] == 0, stats
expected = list(range(11000)) if mode == "append" else list(range(1000, 10000)) if mode == "prefix" else list(range(9000))
if action == "clear": expected = list(payload) if mode == "append" else []
if action.startswith("input"): expected = sorted([*range(10000), *payload])
assert list(values) == expected
assert len(values) == len(expected)
values._check()
if action in ("iterator", "range", "gc-iterator"):
    first, alias = held[0]
    assert (first, consume(alias)) == expected_aliased
    if action == "gc-iterator": assert finalized == [True]
elif action == "index": assert list(held[0]) == expected_aliased
elif action == "fields": assert [list(block) for block in held[0]] == expected_aliased
elif action == "dict":
    assert held[0]["_len"] == 10000
    assert [item for block in held[0]["_lists"] for item in block] == expected
elif action in ("helper", "subtype"):
    assert helper_calls == 1000, helper_calls
    if action == "subtype": assert type(values) is derived
elif action == "input-identity": assert values[10500] is payload[500]
print(json.dumps({"mode": mode, "action": action, "hooks": len(hooks), "length": len(values)}))
"""


@requires_hooks
@pytest.mark.parametrize("mode", ["prefix", "suffix", "append"])
@pytest.mark.parametrize(
    "action",
    [
        "iterator",
        "range",
        "gc-iterator",
        "dict",
        "index",
        "reinstall",
        "fields",
        "clear",
        "helper",
        "subtype",
    ],
)
def test_staging_revalidates_gc_aliases_dictionaries_helpers_and_configuration(
    tmp_path: Path, mode, action
):
    result = run_python(_STAGE_REENTRY, [mode, action], cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == mode
    assert report["action"] == action
    assert report["hooks"] == 1


@requires_hooks
@pytest.mark.parametrize("action", ["input", "input-identity"])
def test_append_staging_revalidates_mutable_payload_identity(tmp_path: Path, action):
    result = run_python(_STAGE_REENTRY, ["append", action], cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "append"
    assert report["action"] == action
    assert report["hooks"] == 1


_UPDATE_INPUTS = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    trace = []
    armed = False
    error = MemoryError("iterable allocation refused")
    mode = sys.argv[1]
    class Integer(int):
        def __lt__(self, other):
            if armed: trace.append(["lt", int(self), int(other)])
            return super().__lt__(other)
        def __gt__(self, other):
            if armed: trace.append(["gt", int(self), int(other)])
            return super().__gt__(other)
    class List(list):
        def __iter__(self):
            if armed: trace.append("iter")
            return super().__iter__()
    class Tuple(tuple):
        def __iter__(self):
            if armed: trace.append("iter")
            return super().__iter__()
    values = ctor(range(10000))
    values._reset(64)
    original = list(values)
    data = list(range(10000, 11000))
    if mode == "decreasing": data.reverse()
    elif mode == "overlap": data = list(range(9500, 10500))
    elif mode == "ties": data = [int("9999") for _ in range(1000)]
    elif mode == "small": data = data[:10]
    elif mode == "huge": data[-1] = 1 << 63
    elif mode == "bool": data[64] = True
    elif mode == "int-subtype": data[64] = Integer(data[64])
    model = sorted([*original, *data])
    if mode.startswith("generator"):
        def generate():
            for index, value in enumerate(data):
                trace.append(["yield", index])
                if mode == "generator-error" and index == 500: raise error
                yield value
        payload = generate()
    else:
        payload = List(data) if mode == "list-subtype" else Tuple(data) if mode == "tuple-subtype" else tuple(data)
    armed = True
    caught = None
    try: result = values.update(iterable=payload)
    except Exception as problem:
        caught = [type(problem).__name__, str(problem), problem is error]
        result = None
    armed = False
    if mode == "generator-error": model = original
    actual = list(values)
    assert len(actual) == len(model)
    assert all(left is right for left, right in zip(actual, model, strict=True))
    values._check()
    return dict(trace=trace, error=caught, result=result, values=actual, length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize(
    "mode",
    [
        "decreasing",
        "overlap",
        "ties",
        "small",
        "huge",
        "bool",
        "int-subtype",
        "generator",
        "generator-error",
        "list-subtype",
        "tuple-subtype",
    ],
)
def test_batch_update_preserves_input_protocols_values_and_identities(
    tmp_path: Path, mode
):
    result = run_python(_UPDATE_INPUTS, [mode], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before


def test_keyed_batch_update_keeps_key_and_equality_tie_order():
    backend = require_backend("sortedcontainers")

    def observe(ctor):
        trace = []
        armed = False

        def key(item):
            if armed:
                trace.append(item[0])
            return item[1]

        values = ctor([("a", 0), ("b", 0), ("c", 1)], key=key)
        assert inspect.isfunction(type(values).update)
        armed = True
        assert values.update([("d", 0), ("e", 1), ("f", 0)]) is None
        armed = False
        values._check()
        return list(values), trace

    assert observe(SortedList) == observe(backend.SortedList)


_METADATA_PROPERTIES = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    placement, name = sys.argv[1:]
    values = ctor(range(10000))
    values._reset(64)
    values.bisect_left(4999)
    values.__delitem__(slice(None, 1000))
    owner = ctor if placement == "class" else sortedcontainers.SortedList
    missing = object()
    previous = owner.__dict__.get(name, missing)
    trace = []
    def get(self):
        trace.append("get")
        return self.__dict__[name]
    def set(self, value):
        trace.append("set")
        self.__dict__[name] = value
    setattr(owner, name, property(get, set))
    caught = None
    try: values.__delitem__(slice(None, 1000))
    except Exception as error: caught = [type(error).__name__, str(error)]
    finally:
        if previous is missing: delattr(owner, name)
        else: setattr(owner, name, previous)
    values._check()
    return dict(trace=trace, error=caught, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("placement", ["class", "base"])
@pytest.mark.parametrize("name", ["_load", "_lists"])
def test_metadata_properties_after_warm_calls_preserve_descriptor_dispatch(
    tmp_path: Path, placement, name
):
    result = run_python(
        _METADATA_PROPERTIES, [placement, name], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"]


_STORED_METADATA_KEY = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    mode, action = sys.argv[1:]
    size = 10000 if mode == "update" else 512
    values = ctor(range(size))
    values._reset(8)
    values.bisect_left(255)
    trace = []
    armed = False
    fired = False
    class Key(str):
        def __eq__(self, other):
            nonlocal fired
            if armed:
                trace.append(["eq", str(other)])
                if action == "finalize" and not fired:
                    fired = True
                    trace.append("before")
                    values.__dict__.pop("_payload")
                    trace.append("after")
            return super().__eq__(other)
        __hash__ = str.__hash__
    class Payload:
        def __del__(self):
            if armed: trace.append("finalize")
    metadata = values.__dict__
    length = metadata.pop("_len")
    metadata[Key("_len")] = length
    del metadata
    values._payload = Payload()
    armed = True
    caught = None
    result = None
    try:
        if mode == "slice": result = values.__delitem__(slice(None, 53))
        elif mode == "update": result = values.update(tuple(range(size, size + 1000)))
        else: result = values.bisect_left(20 if mode == "inside" else 999999)
    except Exception as error: caught = [type(error).__name__, str(error)]
    armed = False
    values.__dict__ = {str(key): value for key, value in values.__dict__.items()}
    values.__dict__.pop("_payload", None)
    values._check()
    return dict(trace=trace, error=caught, result=result, values=list(values), length=len(values))

print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@pytest.mark.parametrize("mode", ["inside", "outside", "slice", "update"])
@pytest.mark.parametrize("action", ["trace", "finalize"])
def test_stored_string_subclass_metadata_keys_keep_python_lookup_callbacks(
    tmp_path: Path, mode, action
):
    result = run_python(_STORED_METADATA_KEY, [mode, action], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    # CPython's adaptive attribute cache varies the number of probes for this
    # deliberately nonstandard private dictionary, even between upstream calls.
    # Require callbacks and their finalizer order, rather than cache probe counts.
    original_trace = before.pop("trace")
    actual_trace = current.pop("trace")
    assert current == before
    assert [event for event in actual_trace if isinstance(event, str)] == [
        event for event in original_trace if isinstance(event, str)
    ]
    assert bool(actual_trace) == bool(original_trace)
    if mode == "inside":
        assert actual_trace == []
    elif action == "finalize":
        assert [event for event in actual_trace if isinstance(event, str)] == [
            "before",
            "finalize",
            "after",
        ]
