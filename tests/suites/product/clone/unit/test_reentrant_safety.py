"""Live container mutation must remain safe across allocation-triggered collection.

The collector waits until the current child has an owned traversal reference,
then mutates its parent. Independent expected outcomes come from Python's live
list and dictionary iterator rules, including split-dictionary insertion order.
Each observer runs in a deadline-owned subprocess so a dangling native pointer
or partially initialized object cannot take down the main test worker.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest


_SCRIPT = """
import gc
import sys
from unstd.clone import structural

mode, backend = sys.argv[1:]
if backend == 'python':
    for name in list(vars(structural)):
        if name.startswith('_native_'):
            setattr(structural, name, None)

first = [11, 12]
second = [21, 22]
if mode.startswith('list-'):
    source = [first, second]
elif mode == 'dict-split-reorder':
    class Holder:
        pass
    holder = Holder()
    holder.first = first
    holder.second = second
    source = vars(holder)
else:
    # Reserve a deletion hole and spare insertion capacity so the same-size
    # key-change case appends an entry instead of compacting during resize.
    source = {'first': first, 'second': second, 'spare': None}
    del source['spare']

baseline = sys.getrefcount(first)
mutated = False
active = False
native_depth = 0
native_kernels = tuple(getattr(structural, name, None) for name in (
    '_native_clone', '_native_graph', '_native_root_graph',
    '_native_record_graph', '_native_walk_graph', '_native_records'))

def profile(frame, event, arg):
    global native_depth
    if any(arg is kernel for kernel in native_kernels):
        if event == 'c_call':
            native_depth += 1
        elif event in ('c_return', 'c_exception'):
            native_depth -= 1

def mutate(phase, info):
    global mutated
    if not active or mutated or phase != 'start':
        return
    if backend == 'native' and native_depth == 0:
        return
    # A graph memo also pins completed sources. The fault-injection build
    # identifies the source whose allocator is actually running, distinguishing
    # this traversal step from later memo commit allocations.
    if backend == 'native':
        if getattr(sys, '_unstd_clone_fault_source', None) != id(first):
            return
    if sys.getrefcount(first) <= baseline:
        return
    mutated = True
    if mode == 'list-replace':
        source[1] = [99]
    elif mode == 'list-clear':
        source.clear()
    elif mode == 'list-grow':
        source.extend([[99]] * 200)
    elif mode == 'dict-replace':
        source['second'] = [99]
    elif mode == 'dict-clear':
        source.clear()
    elif mode in ('dict-keys-change', 'dict-split-reorder'):
        del source['first']
        source['third'] = [99]
    elif mode == 'dict-rebuild':
        source.clear()
        source.update(newfirst=[98], newlast=[99])
    else:
        raise AssertionError(mode)

gc.collect()
gc.callbacks.append(mutate)
gc.set_threshold(1, 1, 1)
gc.enable()
if backend == 'native':
    sys.setprofile(profile)
active = True
error = None
try:
    actual = structural.deep(source)
except RuntimeError as observed:
    error = str(observed)
finally:
    active = False
    sys.setprofile(None)
    gc.callbacks.remove(mutate)

if not mutated:
    # Recent CPython maintenance releases may run the collector only at Python
    # safe points, so there is no in-C interleaving to test on those builds.
    sys.exit(77)

if mode in ('dict-clear', 'dict-keys-change'):
    expected = 'dictionary changed size' if mode == 'dict-clear' else 'dictionary keys changed'
    assert error and expected in error, (mode, error)
else:
    assert error is None, (mode, error)
    if mode == 'list-replace':
        assert actual == [[11, 12], [99]], actual
    elif mode == 'list-clear':
        assert actual == [[11, 12]], actual
    elif mode == 'list-grow':
        assert actual == [[11, 12], [21, 22]] + [[99]] * 200
        assert len({id(branch) for branch in actual}) == 3
        assert all(branch is actual[2] for branch in actual[2:])
    elif mode == 'dict-replace':
        assert list(actual.items()) == [('first', [11, 12]), ('second', [99])], actual
    elif mode == 'dict-split-reorder':
        assert list(actual.items()) == [('first', [11, 12]), ('third', [99])], actual
    elif mode == 'dict-rebuild':
        assert list(actual.items()) == [('first', [11, 12]), ('newlast', [99])], actual
    branches = actual if type(actual) is list else actual.values()
    assert all(type(branch) is list and branch is not first and branch is not second
               for branch in branches)
    assert first == [11, 12]
"""


@pytest.mark.parametrize("backend", ["python", "native"])
@pytest.mark.parametrize(
    "mutation",
    [
        "list-replace",
        "list-clear",
        "list-grow",
        "dict-replace",
        "dict-clear",
        "dict-keys-change",
        "dict-split-reorder",
        "dict-rebuild",
    ],
)
def test_collector_mutates_parent_after_traversal_pins_current_child(
    backend: str, mutation: str
) -> None:
    if (
        backend == "native"
        and importlib.util.find_spec("unstd.clone._clone_native") is None
    ):
        pytest.skip("native clone extension is not built")
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [sys.executable, "-X", "faulthandler", "-c", _SCRIPT, mutation, backend],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("collector deferred until traversal released the current child")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_CLASS_SCRIPT = """
import dataclasses
import gc
import sys
from unstd.clone import structural

collection, backend = int(sys.argv[1]), sys.argv[2]
if backend == 'python':
    for name in list(vars(structural)):
        if name.startswith('_native_'):
            setattr(structural, name, None)

@dataclasses.dataclass
class Row:
    values: list

first = Row([[11, 12]])
source = [first, Row([[21, 22]]), Row([[31, 32]])]
baseline = sys.getrefcount(first)
active = False
mutated = False
seen = 0
finalized = []
native_depth = 0
native_kernels = tuple(getattr(structural, name, None) for name in (
    '_native_clone', '_native_graph', '_native_root_graph',
    '_native_record_graph', '_native_walk_graph', '_native_records'))

def profile(frame, event, arg):
    global native_depth
    if any(arg is kernel for kernel in native_kernels):
        if event == 'c_call':
            native_depth += 1
        elif event in ('c_return', 'c_exception'):
            native_depth -= 1

def finalize(self):
    finalized.append(id(self))

def mutate(phase, info):
    global seen, mutated
    if not active or mutated or phase != 'start':
        return
    if backend == 'native' and native_depth == 0:
        return
    if sys.getrefcount(first) <= baseline:
        return
    seen += 1
    if seen == collection:
        mutated = True
        Row.__del__ = finalize

gc.collect()
gc.callbacks.append(mutate)
gc.set_threshold(1, 1, 1)
gc.enable()
if backend == 'native':
    sys.setprofile(profile)
active = True
try:
    actual = structural.deep(source)
finally:
    active = False
    sys.setprofile(None)
    gc.callbacks.remove(mutate)
if not mutated:
    sys.exit(77)
assert finalized == [], ('abandoned native twins ran finalizers', finalized, collection)
assert actual == source
assert len(actual) == 3
for twin, original in zip(actual, source):
    assert type(twin) is Row and twin is not original
    assert twin.values is not original.values
    assert twin.values[0] is not original.values[0]
# Keep interpreter shutdown separate from the observed copy operation.
del Row.__del__
"""


@pytest.mark.parametrize("backend", ["python", "native"])
@pytest.mark.parametrize("collection", [1, 2, 3, 4])
def test_gc_installed_finalizer_never_observes_abandoned_dataclass_twins(
    backend: str, collection: int
) -> None:
    if (
        backend == "native"
        and importlib.util.find_spec("unstd.clone._clone_native") is None
    ):
        pytest.skip("native clone extension is not built")
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            _CLASS_SCRIPT,
            str(collection),
            backend,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("collector did not reach this allocation while the row was pinned")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gc_observer_can_traverse_every_visible_destination_dictionary() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    code = """
import dataclasses
import gc
from unstd import clone

@dataclasses.dataclass
class Row:
    values: list

marker = complex(345.0, 678.0)
source = {'sentinel': marker, 'record': Row([1])}
source.update({str(index): [index] for index in range(80)})
expected_keys = list(source)
partial = []
observations = 0
active = False

def inspect(phase, info):
    global observations
    if not active or phase != 'start':
        return
    observations += 1
    for value in gc.get_referrers(marker):
        if type(value) is dict and value is not source and value.get('sentinel') is marker:
            if list(value) != expected_keys[:len(value)]:
                partial.append(list(value))
            # Traversing all published values must always be safe as well.
            repr(value)

gc.collect()
gc.callbacks.append(inspect)
gc.set_threshold(1, 1, 1)
gc.enable()
active = True
try:
    actual = clone.deep(source)
finally:
    active = False
    gc.callbacks.remove(inspect)
assert observations, 'GC observer never ran'
assert partial == [], ('invalid destination prefix exposed through GC', partial)
assert list(actual) == expected_keys
assert actual['sentinel'] is marker
assert actual is not source
for key, value in actual.items():
    if key == 'record':
        assert type(value) is Row and value is not source[key]
        assert value.values == [1] and value.values is not source[key].values
    elif key != 'sentinel':
        assert type(value) is list and value == source[key] and value is not source[key]
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal test source
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


_CLASS_BOUNDARY_SCRIPT = """
import dataclasses
import gc
import sys
from unstd.clone import structural

mutation = sys.argv[1]
if all(getattr(structural, name, None) is None for name in ('_native_record_graph', '_native_walk_graph')):
    sys.exit(77)

@dataclasses.dataclass
class Row:
    values: list

source = [Row([11]), Row([21]), Row([31])]
original_dictionary = vars(Row)['__dict__']
reads = []
allocated = []
finalized = []
updates = []
source_ids = {id(value) for value in source}
mutated = False
active = False

def getter(self, name):
    if name == '__dict__':
        reads.append(id(self))
    return object.__getattribute__(self, name)

class Destination:
    def __init__(self, owner):
        self.owner = owner

    @property
    def update(self):
        updates.append(('lookup', id(self.owner)))
        def apply(state):
            updates.append(('apply', id(self.owner)))
            original_dictionary.__get__(self.owner, Row).update(state)
        return apply

def destination(self, name):
    if name == '__dict__':
        reads.append(id(self))
        if id(self) not in source_ids:
            return Destination(self)
    return object.__getattribute__(self, name)

def allocator(cls, *args):
    twin = object.__new__(cls)
    allocated.append(id(twin))
    return twin

def finalizer(self):
    finalized.append(id(self))

def mutate(phase, info):
    global mutated
    if not active or mutated or phase != 'start':
        return
    # The validation-only allocation wrapper exposes exactly the boundary
    # after native chose the default allocator and before it allocates the twin.
    if getattr(sys, '_unstd_clone_fault_allocation', None) != 6:
        return
    mutated = True
    if mutation == 'new':
        Row.__new__ = staticmethod(allocator)
    elif mutation == 'getter':
        Row.__getattribute__ = getter
    elif mutation == 'destination':
        Row.__getattribute__ = destination
    else:
        Row.__del__ = finalizer

gc.collect()
gc.callbacks.append(mutate)
gc.set_threshold(1, 1, 1)
active = True
try:
    actual = structural.deep(source)
finally:
    active = False
    gc.callbacks.remove(mutate)
if not mutated:
    sys.exit(77)
assert type(actual) is list and len(actual) == 3
assert finalized == [], ('abandoned twin after class mutation', finalized)
if mutation == 'new':
    # The first object allocator was already selected. Subsequent rows must
    # observe the newly installed allocator exactly once each.
    assert allocated == [id(actual[1]), id(actual[2])], allocated
elif mutation in ('getter', 'destination'):
    expected = [identity for pair in zip(source, actual)
                for identity in (id(pair[0]), id(pair[1]))]
    assert reads == expected, (mutation, reads, expected)
    if mutation == 'destination':
        expected_updates = [entry for value in actual
                            for entry in (('lookup', id(value)), ('apply', id(value)))]
        assert updates == expected_updates, (updates, expected_updates)
else:
    del Row.__del__
for twin, original in zip(actual, source):
    assert type(twin) is Row and twin is not original
    assert twin.values == original.values and twin.values is not original.values
"""


@pytest.mark.parametrize("mutation", ["new", "getter", "destination", "finalizer"])
def test_class_changes_at_native_allocation_finish_the_selected_twin(
    mutation: str,
) -> None:
    pytest.importorskip("unstd.clone._clone_native")
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [sys.executable, "-X", "faulthandler", "-c", _CLASS_BOUNDARY_SCRIPT, mutation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("validation-only forced-GC allocator is not enabled")
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.skipif(
    sys.version_info >= (3, 14),
    reason="CPython 3.14 guards the machine stack instead of a C recursion counter",
)
def test_native_record_stack_pressure_preserves_shared_reference_identity() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    if not hasattr(native, "clone_record_graph") and not hasattr(
        native, "clone_walk_graph"
    ):
        pytest.skip("native graph record batching is unavailable")
    code = """
import ctypes
import dataclasses
from unstd.clone import structural

enter = ctypes.pythonapi.Py_EnterRecursiveCall
enter.argtypes = [ctypes.c_char_p]
enter.restype = ctypes.c_int
leave = ctypes.pythonapi.Py_LeaveRecursiveCall
leave.argtypes = []
leave.restype = None
entered = 0
exhausted = False
try:
    while entered < 20000:
        try:
            enter(b' while simulating a nested native caller')
        except RecursionError:
            exhausted = True
            break
        entered += 1
finally:
    for _ in range(entered):
        leave()
assert exhausted
pressure = entered - 100
assert pressure > 0

@dataclasses.dataclass
class Row:
    values: object

calls = 0
class Foreign:
    def __deepcopy__(self, memo):
        global calls
        calls += 1
        return [7]

values = [1]
for _ in range(100):
    values = [values]
shared = Row({'early': Foreign(), 'deep': values})
source = [shared, shared]
# Warm admission under the real native backend before testing both routes.
structural.deep(source)
native_functions = {name: value for name, value in vars(structural).items()
                    if name.startswith('_native_')}
for accelerated in (False, True):
    calls = 0
    for name, value in native_functions.items():
        setattr(structural, name, value if accelerated else None)
    entered = 0
    try:
        for _ in range(pressure):
            enter(b' while simulating a nested native caller')
            entered += 1
        actual = structural.deep(source)
    finally:
        for _ in range(entered):
            leave()
    assert actual[0] is actual[1], 'C stack pressure split a shared reference'
    assert actual[0] is not shared
    assert calls == 1, ('stack pressure replayed a foreign copy hook', calls)
    assert actual[0].values['early'] == [7]
    left = actual[0].values['deep']
    right = actual[1].values['deep']
    original = values
    for _ in range(101):
        assert type(left) is list and type(right) is list
        assert left is right and left is not original
        left, right, original = left[0], right[0], original[0]
    assert left == right == original == 1
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal test source
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


_LIMIT_SCRIPT = """
import dataclasses
import gc
import sys
from unstd.clone import structural

limit = int(sys.argv[1])
if all(getattr(structural, name, None) is None for name in ('_native_record_graph', '_native_walk_graph')):
    sys.exit(77)

@dataclasses.dataclass
class Row:
    values: object

values = [1]
for _ in range(60):
    values = [values]
shared = Row(values)
source = [shared, shared]
active = False
mutated = False
finalized = []

def finalize(self):
    finalized.append(id(self))

def mutate(phase, info):
    global mutated
    if not active or mutated or phase != 'start':
        return
    if getattr(sys, '_unstd_clone_fault_allocation', None) != 6:
        return
    mutated = True
    sys.setrecursionlimit(limit)
    if limit == 200:
        Row.__del__ = finalize

gc.collect()
gc.callbacks.append(mutate)
gc.set_threshold(1, 1, 1)
active = True
error = None
try:
    actual = structural.deep(source)
except RecursionError as observed:
    error = observed
finally:
    active = False
    sys.setrecursionlimit(1000)
    gc.callbacks.remove(mutate)
if not mutated:
    sys.exit(77)
if limit == 40:
    assert error is not None, 'record C walk bypassed the new Python recursion budget'
else:
    assert error is None, error
    assert finalized == [], ('safe limit change abandoned a twin', finalized)
    del Row.__del__
    assert actual[0] is actual[1], 'safe limit change lost shared identity'
    assert actual[0] is not shared
    left, right, original = actual[0].values, actual[1].values, values
    for _ in range(61):
        assert type(left) is type(right) is type(original) is list
        assert left is right and left is not original
        left, right, original = left[0], right[0], original[0]
    assert left == right == original == 1
"""


@pytest.mark.parametrize("limit", [40, 200])
def test_gc_lowered_recursion_limit_retains_record_depth_and_alias_contracts(
    limit: int,
) -> None:
    pytest.importorskip("unstd.clone._clone_native")
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, literal code and closed choices
        [sys.executable, "-X", "faulthandler", "-c", _LIMIT_SCRIPT, str(limit)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode == 77:
        pytest.skip("validation-only forced-GC allocator is not enabled")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gc_observer_cannot_retrieve_a_partial_destination_dictionary() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    code = """
import gc
from unstd import clone

marker = complex(345.0, 678.0)
source = {'sentinel': marker}
source.update({str(index): [index] for index in range(80)})
expected_keys = list(source)
partial = []
observations = 0
active = False

def inspect(phase, info):
    global observations
    if not active or phase != 'start':
        return
    observations += 1
    for value in gc.get_referrers(marker):
        if type(value) is dict and value is not source and value.get('sentinel') is marker:
            if list(value) != expected_keys:
                partial.append(list(value))
            # Traversing all published values must always be safe as well.
            repr(value)

gc.collect()
gc.callbacks.append(inspect)
gc.set_threshold(1, 1, 1)
gc.enable()
active = True
try:
    actual = clone.deep(source)
finally:
    active = False
    gc.callbacks.remove(inspect)
assert observations, 'GC observer never ran'
assert partial == [], ('private destination escaped through GC', partial)
assert list(actual) == expected_keys
assert actual['sentinel'] is marker
assert actual is not source
for key, value in actual.items():
    if key != 'sentinel':
        assert type(value) is list and value == source[key] and value is not source[key]
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal test source
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
