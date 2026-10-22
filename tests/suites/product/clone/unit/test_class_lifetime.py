"""Per-class acceleration metadata must not own an ephemeral class's lifetime.

The original strong admission and field caches retained every generated class.
Weak references observe collection independently of either cache implementation.
"""

import copy
import dataclasses
import gc
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
