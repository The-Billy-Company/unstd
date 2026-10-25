"""Memo-published destinations expose valid prefixes and survive reentrant hooks."""

from __future__ import annotations

import subprocess
import sys

import pytest


_SCRIPT = """
import copy
import dataclasses
import gc
import sys
import weakref
from unstd import clone
from unstd.clone import structural

mode, backend = sys.argv[1:]
if backend == 'python':
    for name in list(vars(structural)):
        if name.startswith('_native_'):
            setattr(structural, name, None)
elif getattr(structural, '_native_graph', None) is None:
    sys.exit(77)

@dataclasses.dataclass
class Row:
    values: list

class Tracked:
    pass

references = []
source = None
tail = [2]

class Hook:
    def __deepcopy__(self, memo):
        parent = memo[id(source)]
        assert type(parent) is list
        # These operations would dereference any exposed NULL list slots.
        repr(parent)
        prefix = list(parent)
        assert len(prefix) == 1 and type(prefix[0]) is Row
        assert prefix[0].values == [1]
        if mode == 'append':
            parent.append('inserted')
        elif mode == 'clear':
            parent.clear()
        elif mode == 'replace':
            parent[:] = ['replacement']
        elif mode == 'cycle':
            parent.clear()
            parent.append(parent)
        elif mode == 'error':
            parent.clear()
            parent.append(parent)
            fresh = Tracked()
            references.append(weakref.ref(fresh))
            parent.append(fresh)
            raise LookupError('child copy failed')
        else:
            raise AssertionError(mode)
        return {'tail': copy.deepcopy(tail, memo), 'parent': parent}

source = [Row([1]), Hook(), tail]
if mode == 'error':
    try:
        clone.deep(source)
    except LookupError as error:
        assert str(error) == 'child copy failed'
    else:
        raise AssertionError('copy failure did not propagate')
    gc.collect()
    assert references and all(ref() is None for ref in references), 'untracked failed cycle leaked'
else:
    actual = clone.deep(source)
    assert gc.is_tracked(actual), 'memo-published cyclic destination is untracked'
    if mode == 'append':
        assert len(actual) == 4
        assert type(actual[0]) is Row and actual[0].values == [1]
        assert actual[0] is not source[0]
        assert actual[1] == 'inserted'
        copied = actual[2]
    elif mode == 'clear':
        assert len(actual) == 2
        copied = actual[0]
    elif mode == 'replace':
        assert len(actual) == 3 and actual[0] == 'replacement'
        copied = actual[1]
    else:
        assert len(actual) == 3 and actual[0] is actual
        copied = actual[1]
    assert type(copied) is dict
    assert copied['parent'] is actual
    assert copied['tail'] is actual[-1]
    assert actual[-1] == [2] and actual[-1] is not tail
assert type(source) is list and len(source) == 3
assert type(source[0]) is Row and source[0].values == [1]
assert type(source[1]) is Hook and source[2] is tail
"""


@pytest.mark.parametrize("backend", ["python", "native"])
@pytest.mark.parametrize("mutation", ["append", "clear", "replace", "cycle", "error"])
def test_foreign_hook_can_inspect_and_mutate_its_memo_published_parent(
    backend: str, mutation: str
) -> None:
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [sys.executable, "-X", "faulthandler", "-c", _SCRIPT, mutation, backend],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("native graph copying is unavailable")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_EXPORT_SCRIPT = """
import gc
import sys
from unstd.clone import _clone_native as native

marker = 'clone-export-marker-' + str(id(object()))
first, second = [marker], [marker]
source = [first, second, first]
memo = {}
known = {id(value) for value in gc.get_referrers(source) if type(value) is list}
observed = False
violations = []

def inspect(phase, info):
    global observed
    if phase != 'start' or getattr(sys, '_unstd_clone_fault_allocation', None) != 7:
        return
    observed = True
    # A pin list under construction can contain NULL slots. A collector must
    # never discover it, even though its initialized prefix owns the source.
    for parent in gc.get_referrers(source):
        if type(parent) is list and id(parent) not in known:
            if parent is not memo.get(id(memo)):
                violations.append('private pin-list scratch escaped')
            repr(parent)
    for leaf in gc.get_referrers(marker):
        if type(leaf) is not list or leaf is first or leaf is second:
            continue
        if len(leaf) != 1 or leaf[0] is not marker:
            continue
        # A collector that can clear this scratch mapping can corrupt later
        # memo publication. Only the caller's actual memo may expose entries.
        for parent in gc.get_referrers(leaf):
            if type(parent) is dict and parent is not memo:
                if parent.get(id(first)) is leaf or parent.get(id(second)) is leaf:
                    violations.append('private memo scratch escaped')

gc.callbacks.append(inspect)
try:
    actual = native.clone_graph(source, memo)
finally:
    gc.callbacks.remove(inspect)
if not observed:
    sys.exit(77)
assert not violations, violations
assert actual is not NotImplemented
assert actual == source and actual is memo[id(source)]
assert actual[0] is actual[2] is memo[id(first)]
assert actual[1] is memo[id(second)]
assert actual[0] is not first and actual[1] is not second
"""


def test_native_memo_export_scratch_is_hidden_from_collector_mutation() -> None:
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal audit
        [sys.executable, "-X", "faulthandler", "-c", _EXPORT_SCRIPT],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("requires the allocation-boundary validation build")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_FAULT_SCRIPT = """
import dataclasses
import gc
import sys
import weakref
from unstd import clone
from unstd.clone import _clone_native as native

phase = int(sys.argv[1])
# Confirm that this process imported the fault-injected pointer-map build.
sys._unstd_clone_fault_after = 0
sys._unstd_clone_fault_phase = 12
available = False
try:
    clone.deep([{'items': [index]} for index in range(80)])
except MemoryError:
    available = True
finally:
    del sys._unstd_clone_fault_after
    del sys._unstd_clone_fault_phase
if not available:
    sys.exit(77)

@dataclasses.dataclass
class Row:
    values: object

class Tracked:
    pass

references = []
class Hook:
    def __deepcopy__(self, memo):
        twin = Tracked()
        twin.values = [7]
        references.append(weakref.ref(twin))
        return twin

shared = [5]
payload = {'nested': [[1], [2]], 'shared': shared}
records = [Row([1]), Hook(), Row(payload), shared]
payload['parent'] = records
pure = [{'items': [index]} for index in range(80)]
cases = [list(range(12)), tuple(range(4)), {'id': 1}, pure, Row([1]), records]
failures = 0
successes = 0

def recursion_boundary():
    def descend(level):
        try:
            return descend(level + 1)
        except RecursionError:
            return level
    return descend(0)

original_boundary = recursion_boundary()

def attempt(value, position):
    global failures, successes
    sys._unstd_clone_fault_after = position
    sys._unstd_clone_fault_phase = phase
    try:
        # The public graph path reserves lists without slicing. Its retained
        # strict-tree helper still bulk-slices scalar lists, so audit that
        # allocation family explicitly rather than claiming an unused branch.
        operation = native.clone_checked if phase == 5 and value is cases[0] else clone.deep
        result = operation(value)
    except MemoryError:
        failures += 1
    else:
        successes += 1
        if value is records:
            assert result[0] is not records[0]
            assert type(result[1]) is Tracked and result[1].values == [7]
            assert result[2].values['parent'] is result
            assert result[2].values['shared'] is result[3]
            assert result[3] is not shared
        else:
            assert result == value and type(result) is type(value)
        del result
    finally:
        del sys._unstd_clone_fault_after
        del sys._unstd_clone_fault_phase

for position in (0, 1, 2, 3, 5, 8, 13, 21, 34):
    for value in cases:
        attempt(value, position)
        gc.collect()
        assert recursion_boundary() == original_boundary, 'native virtual recursion reservation leaked'
        assert all(ref() is None for ref in references), 'failed partial memo retained a clone'
        assert records[2].values is payload and payload['parent'] is records
        assert payload['shared'] is records[3] is shared and shared == [5]
        assert records[0].values == [1]
assert failures, ('allocation family was not exercised', phase)
assert successes, ('recovery never succeeded', phase)
# A clean operation after the campaign must not inherit any aborted memo.
result = clone.deep(records)
assert result[2].values['parent'] is result
assert result[2].values['shared'] is result[3]
"""


@pytest.mark.parametrize("allocation_phase", [1, 2, 3, 4, 5, 6, 7, 9, 10, 12])
def test_allocation_failures_release_partial_graphs_and_leave_a_working_retry(
    allocation_phase: int,
) -> None:
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            _FAULT_SCRIPT,
            str(allocation_phase),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("validation-only allocation fault injector is not enabled")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_DICT_SCRIPT = """
import copy
import dataclasses
import gc
import sys
import weakref
from unstd import clone
from unstd.clone import structural

mode, backend = sys.argv[1:]
if backend == 'python':
    for name in list(vars(structural)):
        if name.startswith('_native_'):
            setattr(structural, name, None)
elif all(getattr(structural, name, None) is None for name in
         ('_native_graph', '_native_root_graph', '_native_record_graph', '_native_walk_graph')):
    sys.exit(77)

@dataclasses.dataclass
class Row:
    values: list

class Tracked:
    pass

references = []
source = None
tail = [2]

class Hook:
    def __deepcopy__(self, memo):
        parent = memo[id(source)]
        assert type(parent) is dict
        repr(parent)
        assert list(parent) == ['first']
        assert type(parent['first']) is Row and parent['first'].values == [1]
        if mode == 'append':
            parent['inserted'] = [3]
        elif mode == 'clear':
            parent.clear()
        elif mode == 'cycle':
            parent.clear()
            parent['self'] = parent
        else:
            parent.clear()
            parent['self'] = parent
            fresh = Tracked()
            references.append(weakref.ref(fresh))
            parent['sentinel'] = fresh
            raise LookupError('dictionary child copy failed')
        return {'tail': copy.deepcopy(tail, memo), 'parent': parent}

source = {'first': Row([1]), 'hook': Hook(), 'tail': tail}
if mode == 'error':
    try:
        clone.deep(source)
    except LookupError as error:
        assert str(error) == 'dictionary child copy failed'
    else:
        raise AssertionError('copy failure did not propagate')
    gc.collect()
    assert references and all(ref() is None for ref in references), 'untracked failed dictionary cycle leaked'
else:
    actual = clone.deep(source)
    assert type(actual) is dict and gc.is_tracked(actual)
    if mode == 'append':
        assert list(actual) == ['first', 'inserted', 'hook', 'tail']
        assert actual['first'].values == [1] and actual['first'] is not source['first']
        assert actual['inserted'] == [3]
    elif mode == 'clear':
        assert list(actual) == ['hook', 'tail']
    else:
        assert list(actual) == ['self', 'hook', 'tail'] and actual['self'] is actual
    assert actual['hook']['parent'] is actual
    assert actual['hook']['tail'] is actual['tail']
    assert actual['tail'] == [2] and actual['tail'] is not tail
assert list(source) == ['first', 'hook', 'tail']
assert source['first'].values == [1] and type(source['hook']) is Hook
assert source['tail'] is tail and tail == [2]
"""


@pytest.mark.parametrize("backend", ["python", "native"])
@pytest.mark.parametrize("mutation", ["append", "clear", "cycle", "error"])
def test_foreign_hook_can_inspect_and_mutate_its_memo_published_dictionary(
    backend: str, mutation: str
) -> None:
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [sys.executable, "-X", "faulthandler", "-c", _DICT_SCRIPT, mutation, backend],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("native graph copying is unavailable")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_OVERRIDE_SCRIPT = """
import copy
import sys
from unstd import clone
from unstd.clone import structural

kind, backend = sys.argv[1:]
if backend == 'python':
    for name in list(vars(structural)):
        if name.startswith('_native_'):
            setattr(structural, name, None)
elif all(getattr(structural, name, None) is None for name in
         ('_native_graph', '_native_root_graph', '_native_record_graph', '_native_walk_graph')):
    sys.exit(77)
calls = 0
parent = None
class Hook:
    def __deepcopy__(self, memo):
        global calls
        calls += 1
        memo[id(parent)] = ['override']
        return 'hook'

hook = Hook()
if kind == 'list':
    parent = [hook]
elif kind == 'dict':
    parent = {'value': hook}
elif kind == 'set':
    parent = {hook}
else:
    parent = (hook,)
source = [parent, parent]
expected = copy.deepcopy(source)
assert expected[0] is expected[1] and calls == 1
calls = 0
actual = clone.deep(source)
assert calls == 1
assert actual == expected, (kind, actual, expected)
assert actual[0] is actual[1], ('final mutable parent memo entry was not restored', kind)
if kind == 'tuple':
    assert type(actual[0]) is list and actual[0] == ['override']
else:
    assert type(actual[0]) is type(parent)
    assert actual[0] is not parent
"""


@pytest.mark.parametrize("backend", ["python", "native"])
@pytest.mark.parametrize("container", ["list", "dict", "set", "tuple"])
def test_final_parent_memo_entry_matches_deepcopy_after_a_descendant_override(
    backend: str, container: str
) -> None:
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            _OVERRIDE_SCRIPT,
            container,
            backend,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("native graph copying is unavailable")
    assert completed.returncode == 0, completed.stdout + completed.stderr
