"""Foreign metadata keys preserve strict traces with matching oracle caches."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python
from unstd.containers import sorted as sorted_


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(
    not sorted_._HAVE_NATIVE, reason="native sorted kernels not built"
)


_METADATA_KEY = r"""
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
                if action == "dict" and not fired:
                    fired = True
                    trace.append("before")
                    replacement = values.__dict__.copy()
                    replacement.pop("_payload")
                    values.__dict__ = replacement
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


import types

def cold(code):
    return code.replace(co_consts=tuple(
        cold(item) if isinstance(item, types.CodeType) else item
        for item in code.co_consts
    ))

def run(ctor):
    # Bytecode caches otherwise differ between consecutive upstream calls:
    # a specialized attribute lookup can repeat a foreign-key comparison and
    # release a replaced dictionary at a different point inside that callback.
    for method in vars(sortedcontainers.SortedList).values():
        if isinstance(method, types.FunctionType):
            method.__code__ = cold(method.__code__)
    return observe(ctor)

print(json.dumps([
    run(sortedcontainers.SortedList),
    run(sortedcontainers.SortedList),
    run(SortedList),
]))
"""


@pytest.mark.parametrize("mode", ["inside", "outside", "slice", "update"])
@pytest.mark.parametrize("action", ["trace", "dict"])
def test_metadata_key_callbacks_and_replaced_dictionary_finalizers_match(
    tmp_path: Path, mode: str, action: str
) -> None:
    result = run_python(_METADATA_KEY, [mode, action], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, repeated_oracle, current = json.loads(result.stdout)
    assert repeated_oracle == before
    assert current == before
    if mode == "inside":
        assert current["trace"] == []
    else:
        assert current["trace"]
    if mode != "inside" and action == "dict":
        assert [event for event in current["trace"] if isinstance(event, str)] == [
            "before",
            "after",
            "finalize",
        ]


_CLASS_METADATA_KEY = r"""
import json, sys, sortedcontainers
from unstd.containers import _sorted_native

placement, operation = sys.argv[1:]
field = {'outside': '_len', 'slice': '_lists', 'update': '_load'}[operation]

def observe(use_native):
    trace = []
    armed = False
    sentinel = LookupError('metadata key sentinel')
    class Key(str):
        def __eq__(self, other):
            if armed and other == field:
                trace.append(str(other))
            return super().__eq__(other)
        __hash__ = str.__hash__
    # setattr normalizes str subclasses. type's namespace retains the actual
    # foreign key, so this exercises the class-dictionary callback boundary.
    def get(self):
        if armed:
            trace.append('get')
            raise sentinel
        return self.__dict__[field]
    def set(self, value):
        self.__dict__[field] = value
    descriptor = property(get, set)
    base = sortedcontainers.SortedList
    if placement == 'base':
        base = type('Base', (base,), {Key(field): descriptor})
        namespace = {}
    else:
        namespace = {Key(field): descriptor}
    owned = type('Owned', (base,), namespace)
    if use_native:
        _sorted_native.install(owned, sortedcontainers.SortedList)
    values = owned(range(10000))
    values._reset(64)
    values.bisect_left(50)
    armed = True
    # Construction can cache the class's foreign-key lookup. Invalidate that
    # type cache so both sides actually execute the armed equality callback.
    setattr(owned, '_metadata_guard_epoch', object())
    caught = None
    try:
        if operation == 'outside': values.bisect_left(999999)
        elif operation == 'slice': values.__delitem__(slice(None, 1000))
        else: values.update(tuple(range(10000, 11000)))
    except Exception as error:
        assert error is sentinel, (type(error).__name__, str(error))
        caught = str(error)
    finally:
        armed = False
    # Upstream update can append before this getter raises, leaving its usual
    # partial state. Compare that complete state instead of imposing rollback.
    if operation != 'update': values._check()
    metadata = {name: values.__dict__[name] for name in
                ('_lists', '_maxes', '_index', '_offset', '_len', '_load')}
    return {'trace':trace, 'caught':caught, 'values':list(values), 'metadata':metadata}

import types

def cold(code):
    return code.replace(co_consts=tuple(
        cold(item) if isinstance(item, types.CodeType) else item
        for item in code.co_consts
    ))

def run(use_native):
    for method in vars(sortedcontainers.SortedList).values():
        if isinstance(method, types.FunctionType):
            method.__code__ = cold(method.__code__)
    return observe(use_native)

print(json.dumps([run(False), run(False), run(True)]))
"""


@pytest.mark.parametrize("placement", ["class", "base"])
@pytest.mark.parametrize("operation", ["outside", "slice", "update"])
def test_layout_declines_foreign_class_keys_before_observable_lookup(
    tmp_path: Path, placement: str, operation: str
) -> None:
    result = run_python(
        _CLASS_METADATA_KEY, [placement, operation], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, repeated_oracle, current = json.loads(result.stdout)
    assert repeated_oracle == before
    assert current == before
    assert current["trace"]
