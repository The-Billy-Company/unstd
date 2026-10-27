"""Bounded root temporal sequences preserve the canonical copy policy."""

from __future__ import annotations

import datetime as dt
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.environment.process import run_python
from unstd import clone
from unstd.clone import structural


if TYPE_CHECKING:
    from pathlib import Path


native = pytest.importorskip("unstd.clone._clone_native")
pytestmark = pytest.mark.skipif(
    not getattr(native, "CLOSED_TEMPORALS", 0),
    reason="native root temporal sequences are not built",
)


@pytest.mark.parametrize("kind", [list, tuple])
@pytest.mark.parametrize("zone", [None, dt.UTC])
@pytest.mark.parametrize("temporal", [dt.date, dt.datetime, dt.time])
def test_temporal_roots_complete_in_the_native_graph(
    monkeypatch: pytest.MonkeyPatch, kind: Any, zone: Any, temporal: Any
) -> None:
    value = (
        dt.date(2026, 1, 1)
        if temporal is dt.date
        else dt.datetime(2026, 1, 1, tzinfo=zone)
        if temporal is dt.datetime
        else dt.time(1, 2, 3, tzinfo=zone)
    )

    def refuse(*_args: Any) -> Any:
        raise AssertionError

    monkeypatch.setattr(structural, "_native_walk_graph", refuse)
    source = kind([value, value, 1, None, "a", b"b", 1.0, True, 2j])
    first, second = clone.deep(source), clone.deep(source)
    assert type(first) is type(second) is kind
    assert first == second == source
    assert first is not second is not source
    assert all(a is b for a, b in zip(first, source, strict=True))
    assert first[0] is first[1] is value


def test_temporal_roots_keep_explicit_memo_aliases() -> None:
    value, sentinel = dt.datetime(2026, 1, 1, tzinfo=dt.UTC), object()
    source = [value, value]
    memo: dict[int, Any] = {id(value): sentinel}
    copied = native.clone_graph(source, memo, structural._walk)
    assert copied == [sentinel, sentinel]
    assert memo[id(source)] is copied
    assert (
        native.clone_graph(source, {id(source): sentinel}, structural._walk) is sentinel
    )
    assert native.clone_graph(source, {}, structural._walk) is NotImplemented


@pytest.mark.parametrize("policy", ["_ATOMS", "_GRAPH_ATOMS", "_TIMEZONES"])
def test_temporal_policy_foreign_membership_retains_python_exception(
    monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    value = dt.datetime(2026, 1, 1)  # noqa: DTZ001 — exercise the naive timezone policy
    selected = type(None) if policy == "_TIMEZONES" else dt.datetime
    failure = LookupError("foreign temporal policy key compared")
    calls: list[object] = []

    class Meta(type):
        def __hash__(cls) -> int:
            return hash(selected)

        def __eq__(cls, other: object) -> bool:
            calls.append(other)
            raise failure

    class Foreign(metaclass=Meta):
        pass

    monkeypatch.setattr(structural, policy, frozenset({Foreign}))
    with pytest.raises(LookupError) as reference:
        structural._walk([value], {})
    expected_calls = calls.copy()
    calls.clear()
    with pytest.raises(LookupError) as actual:
        clone.deep([value])
    assert actual.value is reference.value is failure
    assert calls == expected_calls == [selected]


@pytest.mark.parametrize("policy", ["atoms", "zones", "walk-code"])
def test_temporal_roots_defer_after_canonical_policy_changes(
    monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    value = (
        dt.date(2026, 1, 1) if policy == "atoms" else dt.datetime(2026, 1, 1)  # noqa: DTZ001 — exercise the naive timezone policy
    )
    if policy == "atoms":
        monkeypatch.setattr(structural, "_ATOMS", structural._ATOMS - {dt.date})
    elif policy == "zones":
        monkeypatch.setattr(structural, "_TIMEZONES", frozenset())
    else:

        def changed(_source: Any, _memo: dict[int, Any] | None = None) -> str:
            return "changed"

        monkeypatch.setattr(structural._walk, "__code__", changed.__code__)
    assert native.clone_graph([value], None, structural._walk) is NotImplemented


def test_temporal_roots_keep_foreign_timezone_and_subclass_protocols() -> None:
    calls: list[str] = []

    class Zone(dt.tzinfo):
        def utcoffset(self, value: Any) -> dt.timedelta:
            return dt.timedelta(0)

        def dst(self, value: Any) -> dt.timedelta:
            return dt.timedelta(0)

        def __deepcopy__(self, _memo: dict[int, Any]) -> dt.tzinfo:
            calls.append("zone")
            return dt.timezone(dt.timedelta(hours=2))

    class Date(dt.date):
        def __deepcopy__(self, _memo: dict[int, Any]) -> str:
            calls.append("date")
            return "copied date"

    value, date = dt.datetime(2026, 1, 1, tzinfo=Zone()), Date(2026, 1, 1)
    copied = clone.deep([value, value, date, date])
    assert copied[0] is copied[1] is not value
    assert copied[0].utcoffset() == dt.timedelta(hours=2)
    assert copied[2:] == ["copied date", "copied date"]
    assert calls == ["zone", "date"]


@pytest.mark.parametrize("change", ["policy", "shrink", "grow-foreign", "release"])
def test_temporal_roots_revalidate_at_allocation(change: str, tmp_path: Path) -> None:
    worker = dedent(
        """
        import copy
        import datetime as dt
        import gc
        import sys
        from unstd import clone
        from unstd.clone import structural

        mode = sys.argv[1]
        value = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        source = [value] * 8
        identity = id(source)
        sentinel = object()
        changed = active = False
        calls = []
        class Hook:
            def __deepcopy__(self, memo):
                calls.append('hook')
                return 'copied'
        hook = Hook()
        def mutate(phase, info):
            global changed, source
            if (not active or changed or phase != 'start'
                    or getattr(sys, '_unstd_clone_fault_allocation', None) != 1
                    or getattr(sys, '_unstd_clone_fault_source', None) != identity):
                return
            changed = True
            if mode == 'policy':
                structural._TIMEZONES = frozenset()
            elif mode == 'shrink':
                source[:] = [value] * 2
            elif mode == 'release':
                source = None
            else:
                source.extend([hook, hook])
        copy._deepcopy_dispatch[dt.datetime] = lambda o, memo: sentinel
        gc.collect()
        gc.callbacks.append(mutate)
        active = True
        try:
            actual = clone.deep(source)
        finally:
            active = False
            gc.callbacks.remove(mutate)
        if not changed:
            sys.exit(77)
        if mode == 'policy':
            assert actual == [sentinel] * 8
        elif mode == 'shrink':
            assert actual == [value] * 2
        elif mode == 'release':
            assert source is None and actual == [value] * 8
        else:
            assert actual == [value] * 8 + ['copied', 'copied']
            assert calls == ['hook']
        """
    )
    completed = run_python(worker, (change,), cwd=tmp_path, timeout=20)
    if completed.returncode == 77:
        pytest.skip("requires native allocation-boundary GC injection")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_later_policy_changes_do_not_replay_temporal_prefixes(tmp_path: Path) -> None:
    worker = dedent(
        """
        import copy
        import datetime as dt
        import gc
        import sys
        from unstd import clone
        from unstd.clone import structural

        early = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        late = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
        child = [0]
        source = [early, child, late, early]
        sentinel = object()
        calls = []
        changed = active = False
        def foreign(value, memo):
            calls.append(value)
            return sentinel
        def mutate(phase, info):
            global changed
            if (active and not changed and phase == 'start'
                    and getattr(sys, '_unstd_clone_fault_allocation', None) == 1
                    and getattr(sys, '_unstd_clone_fault_source', None) == id(child)):
                changed = True
                structural._TIMEZONES = frozenset()
        copy._deepcopy_dispatch[dt.datetime] = foreign
        gc.collect()
        gc.callbacks.append(mutate)
        active = True
        try:
            actual = clone.deep(source)
        finally:
            active = False
            gc.callbacks.remove(mutate)
        if not changed:
            sys.exit(77)
        assert actual[0] is actual[3] is early
        assert actual[2] is sentinel
        assert calls == [late]
        """
    )
    completed = run_python(worker, cwd=tmp_path, timeout=20)
    if completed.returncode == 77:
        pytest.skip("requires native allocation-boundary GC injection")
    assert completed.returncode == 0, completed.stdout + completed.stderr
