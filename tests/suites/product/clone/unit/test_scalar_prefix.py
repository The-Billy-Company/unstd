"""The scalar prefix stays safe around allocation and recursive child callbacks."""

from __future__ import annotations

import gc

import pytest

from tests.support.environment.process import run_python


native = pytest.importorskip("unstd.clone._clone_native")


_SCRIPT = r"""
import faulthandler
import gc
import sys
from unstd.clone import _clone_native as native

faulthandler.enable()
mode = sys.argv[1]
shared = [31, 32]
source = list(range(8)) if mode.startswith('allocation-') else [1, 2, shared, shared]
target = source if mode.startswith('allocation-') else shared
mutated = False
active = False
def mutate(phase, info):
    global mutated
    if (active and not mutated and phase == 'start'
            and getattr(sys, '_unstd_clone_fault_allocation', None) == 1
            and getattr(sys, '_unstd_clone_fault_source', None) == id(target)):
        mutated = True
        if mode == 'allocation-shrink':
            source[:] = [7, 8]
        elif mode == 'allocation-clear':
            source.clear()
        elif mode == 'allocation-grow':
            source.extend([shared, shared])
        elif mode == 'child-clear':
            source.clear()
        elif mode == 'child-grow':
            source.extend([shared, shared])
        else:
            raise AssertionError(mode)

gc.collect()
gc.callbacks.append(mutate)
active = True
try:
    result = native.clone_graph(source)
finally:
    active = False
    gc.callbacks.remove(mutate)
if not mutated:
    sys.exit(77)
if mode == 'allocation-shrink':
    assert result == [7, 8], result
elif mode == 'allocation-clear':
    assert result == [], result
elif mode == 'allocation-grow':
    assert result == list(range(8)) + [[31, 32], [31, 32]], result
    assert result[-1] is result[-2] is not shared
elif mode == 'child-clear':
    assert result == [1, 2, [31, 32]], result
    assert result[-1] is not shared
elif mode == 'child-grow':
    assert result == [1, 2] + [[31, 32]] * 4, result
    assert all(value is result[2] for value in result[2:])
    assert result[2] is not shared
assert result is not source
"""


@pytest.mark.parametrize(
    "mode",
    [
        "allocation-shrink",
        "allocation-clear",
        "allocation-grow",
        "child-clear",
        "child-grow",
    ],
)
def test_scalar_prefix_rechecks_live_source_around_allocation(mode, tmp_path):
    completed = run_python(_SCRIPT, (mode,), cwd=tmp_path, timeout=30)
    if completed.returncode == 77:
        pytest.skip("requires native allocation-boundary GC injection")
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("kind", [list, tuple])
def test_scalar_prefix_preserves_later_child_aliases(kind):
    shared = [31, 32]
    source = kind([1, 2, shared, shared])
    result = native.clone_graph(source)
    assert type(result) is kind
    assert result == source
    assert result is not source
    assert result[2] is result[3] is not shared


def test_empty_sequences_after_a_scalar_prefix_keep_valid_collector_state():
    source = [0, [], [], (), ()]
    result = native.clone_graph(source)
    assert result == source
    assert result is not source
    assert result[1] is not source[1]
    assert result[2] is not source[2]
    assert result[1] is not result[2]
    assert gc.is_tracked(result[1])
    assert gc.is_tracked(result[2])
    assert result[3] is result[4] is source[3]
    assert not gc.is_tracked(result[3])


def test_collector_can_only_traverse_initialized_scalar_prefix_tuples(tmp_path):
    code = r"""
import faulthandler
import gc
import sys
from unstd.clone import _clone_native as native
faulthandler.enable()
observations = 0
active = False
def observe(phase, info):
    global observations
    if (active and phase == 'start'
            and getattr(sys, '_unstd_clone_fault_allocation', None) is not None):
        for value in gc.get_objects():
            if type(value) in (list, tuple) and len(value) == 257:
                repr(value)
        observations += 1
gc.collect()
sources = [tuple(range(257)), tuple([1, 2] + [[i] for i in range(255)])]
gc.callbacks.append(observe)
active = True
try:
    for source in sources:
        actual = native.clone_graph(source)
        assert actual == source and actual is not source
finally:
    active = False
    gc.callbacks.remove(observe)
if not observations:
    sys.exit(77)
"""
    completed = run_python(code, cwd=tmp_path, timeout=30)
    if completed.returncode == 77:
        pytest.skip("collector deferred until native traversal completed")
    assert completed.returncode == 0, completed.stdout + completed.stderr


_CACHE_SCRIPT = """
import faulthandler
import gc
import sys
from unstd.clone import _clone_native as native

faulthandler.enable()
mode = sys.argv[1]
# Ten completed children fill the inline memo until storing their parent grows
# it. This boundary follows the parent's complete clone, before the next alias.
expected = [[i] for i in range(10)]
shared = [[i] for i in range(10)]
source = [0, shared, 17, shared]
active = False
mutated = False
def mutate(phase, info):
    global mutated
    if (active and not mutated and phase == 'start'
            and getattr(sys, '_unstd_clone_fault_allocation', None) == 12):
        mutated = True
        if mode == 'shared-change':
            shared[:] = [999]
        elif mode == 'parent-replace':
            source[3] = [901]
        else:
            raise AssertionError(mode)

gc.collect()
gc.callbacks.append(mutate)
active = True
try:
    result = native.clone_graph(source)
finally:
    active = False
    gc.callbacks.remove(mutate)
if not mutated:
    sys.exit(77)
assert result[:3] == [0, expected, 17], result
assert result[1] is not shared
if mode == 'shared-change':
    assert shared == [999]
    assert result[1] is result[3]
else:
    assert result[3] == [901] and result[3] is not source[3]
    assert result[1] is not result[3]
"""


@pytest.mark.parametrize("mode", ["shared-change", "parent-replace"])
def test_repeated_child_reuses_its_completed_clone_after_a_collector_mutation(
    mode, tmp_path
):
    completed = run_python(_CACHE_SCRIPT, (mode,), cwd=tmp_path, timeout=30)
    if completed.returncode == 77:
        pytest.skip("requires the allocation-boundary validation build")
    assert completed.returncode == 0, completed.stdout + completed.stderr
