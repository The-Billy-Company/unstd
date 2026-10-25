"""Per-class acceleration metadata must not own an ephemeral class's lifetime.

The original strong admission and field caches retained every generated class.
Weak references observe collection independently of either cache implementation.
"""

import copy
import dataclasses
import gc
import subprocess
import sys
import weakref

import pytest

from unstd import clone


@pytest.mark.parametrize(
    ("operation", "reference"),
    [(clone.deep, copy.deepcopy), (clone.asdict, dataclasses.asdict)],
    ids=["admission", "fields"],
)
def test_generated_classes_are_released_after_their_instances(operation, reference):
    def exercise(call):
        references = []
        for index in range(32):
            record = dataclasses.make_dataclass(f"Temporary{index}", [("items", list)])
            source = record([index])
            result = call(source)
            items = result["items"] if isinstance(result, dict) else result.items
            assert items == [index]
            items.append(-1)
            assert source.items == [index]
            references.append(weakref.ref(record))
        return references

    control = exercise(reference)
    observed = exercise(operation)
    gc.collect()
    assert all(item() is None for item in control), "stdlib collection control failed"
    assert all(item() is None for item in observed), "clone metadata retained a class"


def test_direct_native_module_deallocation_releases_cached_weakrefs() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    # Clear builtin functions' references to their module to select direct
    # refcount deallocation; cyclic GC's module clear hook is not sufficient.
    code = """
import dataclasses
import importlib.util
import sys
import weakref
from unstd.clone import _clone_native as native
from unstd.clone import structural

spec = importlib.util.spec_from_file_location(
    '_clone_direct_dealloc._clone_native', native.__file__)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

@dataclasses.dataclass
class Row:
    values: object

source = Row([1])
class_reference = weakref.ref(Row)
baseline = sys.getrefcount(class_reference)
for _ in range(5):
    actual = module.clone_dataclass(source, {}, structural._walk)
    assert actual.values == [1] and actual.values is not source.values
assert sys.getrefcount(class_reference) > baseline, 'native cache was not warmed'

module_reference = weakref.ref(module)
module.__dict__.clear()
del module
assert module_reference() is None, 'direct module deallocation was not reached'
assert sys.getrefcount(class_reference) == baseline, 'module state leaked a weakref'
"""
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and literal test source
        [sys.executable, "-X", "faulthandler", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
