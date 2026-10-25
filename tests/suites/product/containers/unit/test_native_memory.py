"""Bounded lifetime, reinstallation and corrupted-state probes for C kernels.

Potentially invalid internal state runs in owned interpreters so a native crash
is a failed probe. Valid histories compare with upstream and a plain list model;
weak references test actual reclamation instead of machine-specific RSS limits.
"""

from __future__ import annotations

import bisect
import gc
import json
import operator
import random
from typing import TYPE_CHECKING
import weakref

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python
from unstd.containers import SortedList
from unstd.containers import sorted as sorted_


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(
    not sorted_._HAVE_NATIVE, reason="native sorted kernels not built"
)


@pytest.mark.parametrize("keyed", [False, True])
def test_repeated_installs_and_ephemeral_types_are_reclaimed(keyed: bool) -> None:
    backend = require_backend("sortedcontainers")
    from unstd.containers import _sorted_native

    base = backend.SortedKeyList if keyed else backend.SortedList

    def create():
        class Owned(base):
            pass

        for _ in range(6):
            _sorted_native.install(Owned, base, keyed=keyed)
        values = Owned(range(64), key=operator.neg) if keyed else Owned(range(64))
        values._reset(4)
        values.bisect_left(31)
        values.add(65)
        values._check()
        return weakref.ref(Owned), weakref.ref(values)

    references = [create() for _ in range(48)]
    for _ in range(3):
        gc.collect()
    assert all(cls() is None and value() is None for cls, value in references)


_REINSTALL = """
import bisect, json, sys
import sortedcontainers
from unstd.containers import _sorted_native

class Owned(sortedcontainers.SortedList):
    pass

_sorted_native.install(Owned, sortedcontainers.SortedList)
values = Owned(range(128))
values._reset(4)
values.bisect_left(63)
armed = True
installs = 0

def reenter():
    global armed, installs
    if armed:
        armed = False
        installs += 1
        _sorted_native.install(Owned, sortedcontainers.SortedList)

class Probe(int):
    def __lt__(self, other):
        reenter()
        return super().__lt__(other)
    def __gt__(self, other):
        reenter()
        return super().__gt__(other)

operation = sys.argv[1]
result = getattr(values, operation)(Probe(63))
model = list(range(128))
if operation == "add":
    bisect.insort(model, 63)
    assert result is None
else:
    expected = getattr(bisect, operation)(model, 63)
    assert result == expected
assert installs == 1
assert len(values) == len(model)
assert list(values) == model
values._check()
print(json.dumps({"operation": operation, "installs": installs, "length": len(values)}))
"""


@pytest.mark.parametrize("operation", ["add", "bisect_left", "bisect_right"])
def test_reinstallation_from_active_comparison_keeps_old_configuration_alive(
    tmp_path: Path, operation: str
) -> None:
    result = run_python(_REINSTALL, [operation], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["operation"] == operation
    assert report["installs"] == 1
    assert report["length"] == (129 if operation == "add" else 128)


def _bucket(value: int) -> int:
    return value % 13


@pytest.mark.parametrize("load", [4, 8, 16])
@pytest.mark.parametrize("keyed", [False, True])
def test_long_mixed_histories_preserve_index_and_model(load: int, keyed: bool) -> None:
    backend = require_backend("sortedcontainers")
    key = _bucket if keyed else None
    actual = SortedList(key=key)
    reference = backend.SortedList(key=key)
    model: list[int] = []
    actual._reset(load)
    reference._reset(load)
    rng = random.Random(74017 + load + keyed)  # noqa: S311 — reproducible histories.

    for step in range(4000):
        operation = rng.randrange(8)
        if len(model) < 16:
            operation = 3
        elif len(model) > 384:
            operation = 2
        value = rng.randrange(-256, 257)
        if operation == 0:
            actual.add(value)
            reference.add(value)
            keys = [_bucket(item) for item in model] if keyed else model
            position = bisect.bisect_right(keys, _bucket(value) if keyed else value)
            model.insert(position, value)
        elif operation == 1:
            actual.discard(value)
            reference.discard(value)
            if value in model:
                model.remove(value)
        elif operation == 2:
            position = rng.randrange(-len(model), len(model))
            assert (
                actual.pop(position) == reference.pop(position) == model.pop(position)
            )
        elif operation == 3:
            batch = [rng.randrange(-256, 257) for _ in range(rng.randrange(1, 17))]
            actual.update(batch)
            reference.update(batch)
            model.extend(batch)
            model.sort(key=key)
        elif operation == 4:
            if value in model:
                actual.remove(value)
                reference.remove(value)
                model.remove(value)
            else:
                for collection in (actual, reference):
                    with pytest.raises(ValueError, match="not in list"):
                        collection.remove(value)
        elif operation == 5:
            start = rng.randrange(len(model) + 1)
            stop = min(len(model), start + rng.randrange(0, 8))
            stride = rng.choice((1, 2))
            del actual[start:stop:stride]
            del reference[start:stop:stride]
            del model[start:stop:stride]
        elif operation == 6:
            position = rng.randrange(-len(model), len(model))
            assert actual[position] == reference[position] == model[position]
            assert actual[::-1] == reference[::-1] == model[::-1]
        else:
            actual._reset(load)
            reference._reset(load)

        assert len(actual) == len(reference) == len(model)
        assert list(actual) == list(reference) == model
        keys = [_bucket(item) for item in model] if keyed else model
        probe = _bucket(value) if keyed else value
        assert actual.bisect_left(value) == bisect.bisect_left(keys, probe)
        assert actual.bisect_right(value) == bisect.bisect_right(keys, probe)
        assert len(model) <= 400
        if step % 17 == 0:
            actual._check()
            reference._check()
    actual._check()


_CORRUPTED = """
import json, sys
import sortedcontainers
from unstd.containers import SortedList, _sorted_native

def probe(name):
    values = SortedList(range(32))
    values._reset(2)
    values.bisect_left(19)
    if name == "outer-list": values._lists = object()
    elif name == "missing-lists": del values._lists
    elif name == "maxes": values._maxes = object()
    elif name == "block": values._lists[0] = object()
    elif name == "empty-lists": values._lists = []
    elif name == "length": values._len = object()
    elif name == "load": values._load = object()
    elif name in ("index-item", "negative-count", "rank-overflow"):
        item = object() if name == "index-item" else -1 if name == "negative-count" else sys.maxsize
        values._index = [0, item, 0]
        values._offset = 0
    elif name == "offset-overflow": values._offset = sys.maxsize
    elif name == "offset-type": values._offset = object()
    else: raise AssertionError(name)
    try:
        if name in ("index-item", "negative-count", "rank-overflow", "offset-overflow"):
            values._loc(2, 1)
        elif name == "offset-type": values._pos(19)
        elif name == "load": values._expand(0)
        else: values.add(0)
    except (TypeError, ValueError, IndexError, OverflowError, AttributeError) as error:
        return type(error).__name__
    raise AssertionError(f"corrupt shape {name} was accepted")

names = ("outer-list", "missing-lists", "maxes", "block", "empty-lists", "length",
    "load", "index-item", "negative-count", "rank-overflow", "offset-overflow", "offset-type")
report = {name: probe(name) for name in names}

for mutation in ("replace", "delete"):
    class Owned(sortedcontainers.SortedList): pass
    _sorted_native.install(Owned, sortedcontainers.SortedList)
    values = Owned([1, 3])
    if mutation == "replace": Owned._unstd_sorted_native_config = object()
    else: del Owned._unstd_sorted_native_config
    try: values.add(2)
    except RuntimeError as error: report[f"config-{mutation}"] = type(error).__name__
    else: raise AssertionError("invalid native capsule was accepted")
print(json.dumps(report))
"""


def test_corrupted_private_shapes_fail_safely_in_owned_worker(tmp_path: Path) -> None:
    result = run_python(_CORRUPTED, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert len(report) == 14
    assert set(report.values()) <= {
        "TypeError",
        "ValueError",
        "IndexError",
        "OverflowError",
        "AttributeError",
        "RuntimeError",
    }
