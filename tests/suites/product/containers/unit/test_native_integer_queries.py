"""Guarded integer queries retain arbitrary-int and comparator semantics."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python
from unstd.containers import SortedList
from unstd.containers import sorted as sorted_


if TYPE_CHECKING:
    from pathlib import Path


requires_native = pytest.mark.skipif(
    not sorted_._HAVE_NATIVE, reason="native sorted kernels not built"
)


@requires_native
@pytest.mark.parametrize("operation", ["bisect_left", "bisect_right", "bisect"])
def test_integer_query_boundaries_and_overflow_match_stdlib(operation: str) -> None:
    model = sorted(
        [-(10**100), -(2**63) - 1, -(2**63), -1, 0, 0, 1, 2**63 - 1, 2**63, 10**100] * 5
    )
    values = SortedList(model)
    values._reset(4)
    oracle = bisect_left if operation == "bisect_left" else bisect_right
    for value in [
        -(10**101),
        -(10**100),
        -(2**63) - 1,
        -(2**63),
        -(2**63) + 1,
        -1,
        0,
        1,
        2**63 - 2,
        2**63 - 1,
        2**63,
        10**100,
        10**101,
    ]:
        assert getattr(values, operation)(value) == oracle(model, value)
    assert list(values) == model
    values._check()


_STORED_INTEGER_CALLBACK = """
import json, sys
import sortedcontainers
from unstd.containers import SortedList

def run(ctor):
    trace = []
    target = None
    armed = False
    def effect():
        nonlocal armed
        if armed:
            armed = False
            trace.append('mutate')
            if sys.argv[2] == 'clear':
                target.clear()
            else:
                replacement = target.__dict__.copy()
                replacement.pop('_payload', None)
                target.__dict__ = replacement
    class Stored(int):
        def __lt__(self, other):
            trace.append('lt')
            effect()
            return super().__lt__(other)
        def __gt__(self, other):
            trace.append('gt')
            effect()
            return super().__gt__(other)
    model = [Stored(i) if i == 64 else i for i in range(128)]
    target = ctor(model)
    if sys.argv[2] == 'finalizer-dict':
        class Payload:
            def __del__(self):
                trace.append('finalize')
                target.__dict__ = target.__dict__.copy()
        target._payload = Payload()
    trace.clear()
    armed = True
    error = None
    result = None
    try:
        result = getattr(target, sys.argv[1])(64)
    except Exception as caught:
        error = [type(caught).__name__, str(caught)]
    target._check()
    return dict(trace=trace, error=error, result=result, values=list(target), length=len(target))
print(json.dumps([run(sortedcontainers.SortedList), run(SortedList)]))
"""


@requires_native
@pytest.mark.parametrize("operation", ["bisect_left", "bisect_right", "bisect"])
@pytest.mark.parametrize("effect", ["clear", "copy-dict", "finalizer-dict"])
def test_exact_integer_query_retains_stored_subclass_callbacks(
    tmp_path: Path, operation: str, effect: str
) -> None:
    result = run_python(
        _STORED_INTEGER_CALLBACK, [operation, effect], cwd=tmp_path, timeout=30
    )
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert "mutate" in current["trace"]
    if effect == "finalizer-dict":
        assert current["trace"].count("finalize") == 1


@requires_native
@pytest.mark.parametrize("operation", ["bisect_left", "bisect_right", "bisect"])
def test_plain_keywords_keep_native_query_and_subtypes_use_python_binding(
    monkeypatch, operation: str
) -> None:
    from sortedcontainers import SortedList as Upstream

    from unstd.containers import _sorted_native

    method = "bisect_right" if operation == "bisect" else operation
    original = getattr(Upstream, method)
    calls = []

    def fallback(self, value):
        calls.append(value)
        return original(self, value)

    monkeypatch.setattr(Upstream, method, fallback)

    class Owned(Upstream):
        pass

    _sorted_native.install(Owned, Upstream)
    values = Owned(range(128))
    expected = 64 if operation == "bisect_left" else 65
    assert getattr(values, operation)(value=64) == expected
    assert calls == []
    trace = []

    class Name(str):
        def __eq__(self, other):
            trace.append(other)
            return super().__eq__(other)

        __hash__ = str.__hash__

    assert getattr(values, operation)(**{Name("value"): 64}) == expected
    assert trace == ["self", "value"]
    assert calls == [64]


_KEYWORD_ERROR_ORDER = """
import json, sys, sortedcontainers
from unstd.containers import SortedList

def observe(ctor):
    values = ctor(range(128))
    trace = []
    error = LookupError("binding refused")
    class Payload:
        def __del__(self): trace.append("finalize")
    class Name(str):
        def __eq__(self, other):
            trace.append(["compare", other])
            replacement = values.__dict__.copy()
            replacement.pop("_payload", None)
            values.__dict__ = replacement
            trace.append("after")
            raise error
        __hash__ = str.__hash__
    values._payload = Payload()
    caught = None
    try:
        getattr(values, sys.argv[1])(**{Name("value"): 64})
    except LookupError as problem:
        assert problem is error
        caught = [type(problem).__name__, str(problem)]
    values._check()
    return dict(trace=trace, caught=caught, values=list(values))
print(json.dumps([observe(sortedcontainers.SortedList), observe(SortedList)]))
"""


@requires_native
@pytest.mark.parametrize("operation", ["bisect_left", "bisect_right", "bisect"])
def test_keyword_subtype_error_and_finalizer_order_matches_upstream(
    tmp_path: Path, operation: str
) -> None:
    result = run_python(_KEYWORD_ERROR_ORDER, [operation], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    before, current = json.loads(result.stdout)
    assert current == before
    assert current["trace"] == [["compare", "self"], "finalize", "after"]
