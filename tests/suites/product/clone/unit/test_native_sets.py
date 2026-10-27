"""Exact sets retain the live Python walk inside the native mixed graph."""

from __future__ import annotations

import signal
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import isolated_environment, run_python
from unstd import clone
from unstd.clone import structural


if TYPE_CHECKING:
    from pathlib import Path


native = pytest.importorskip("unstd.clone._clone_native")


@pytest.mark.parametrize("entry", ["clone_walk_graph", "clone_walk_canonical_graph"])
def test_exact_scalar_sets_complete_without_python_callbacks(entry: str) -> None:
    def refuse(_source: object, _memo: dict[int, object]) -> object:
        raise AssertionError

    source = {"alpha", "beta", 2, (3, "four")}
    memo: dict[int, object] = {}
    copied = getattr(native, entry)(source, refuse, structural._record_admit, memo)
    assert type(copied) is set
    assert copied == source
    assert copied is not source
    assert memo[id(source)] is copied


def test_sets_publish_empty_twins_and_restore_descendant_memo_overrides() -> None:
    observed: list[set[object]] = []
    source: set[object] = set()

    class Hook:
        def __deepcopy__(self, memo: dict[int, object]) -> str:
            partial = memo[id(source)]
            assert type(partial) is set
            observed.append(partial)
            partial.add("visible")
            memo[id(source)] = "overwritten"
            return "copied"

    source.add(Hook())
    copied = clone.deep([source, source])
    assert copied[0] is copied[1] is observed[0]
    assert copied[0] == {"visible", "copied"}
    assert len(source) == 1


def test_set_foreign_children_can_reference_the_published_parent() -> None:
    source: set[object] = set()

    class Node:
        parent: set[object]

        def __deepcopy__(self, memo: dict[int, object]) -> Node:
            twin = Node()
            memo[id(self)] = twin
            parent = memo[id(source)]
            assert type(parent) is set
            twin.parent = parent
            return twin

    source.add(Node())
    copied = clone.deep(source)
    [child] = copied
    assert isinstance(child, Node)
    assert child.parent is copied
    assert copied is not source


def test_set_iteration_retains_live_size_change_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    for enabled in (False, True):
        source: set[object] = set()

        class Hook:
            def __init__(self, parent: set[object]) -> None:
                self.parent = parent

            def __deepcopy__(self, _memo: dict[int, object]) -> str:
                self.parent.add("new item")
                return "copied"

        source.add(Hook(source))
        with monkeypatch.context() as patch:
            if not enabled:
                patch.setattr(structural, "_native_clone", None)
            with pytest.raises(RuntimeError) as error:
                clone.deep(source)
        messages.append(str(error.value))
    assert messages == ["Set changed size during iteration"] * 2


def test_set_hash_failures_keep_exception_identity_and_initialized_memo() -> None:
    failure = LookupError("set hash failure")

    class Copied:
        def __hash__(self) -> int:
            raise failure

    class Hook:
        def __deepcopy__(self, _memo: dict[int, object]) -> Copied:
            return Copied()

    source = {Hook()}
    memo: dict[int, object] = {}
    with pytest.raises(LookupError) as error:
        native.clone_walk_graph(
            source, structural._walk, structural._record_admit, memo
        )
    assert error.value is failure
    assert type(memo[id(source)]) is set
    assert memo[id(source)] == set()


def test_set_callers_keep_explicit_memo_precedence() -> None:
    source = {"original"}
    sentinel = object()
    memo = {id(source): sentinel}
    copied = native.clone_walk_graph(
        source, structural._walk, structural._record_admit, memo
    )
    assert copied is sentinel


def test_set_subclasses_keep_their_copy_protocol() -> None:
    sentinel = object()
    calls: list[dict[int, object]] = []

    class ForeignSet(set[int]):
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            calls.append(memo)
            return sentinel

    source = ForeignSet({1, 2})
    assert clone.deep([source, source]) == [sentinel, sentinel]
    assert len(calls) == 1


@pytest.mark.skipif(
    not hasattr(signal, "setitimer"), reason="interval timers are unavailable"
)
def test_large_native_set_remains_interruptible(tmp_path: Path) -> None:
    worker = dedent(
        """
        import signal
        import sys
        from unstd.clone import _clone_native as native

        class Interrupted(Exception):
            pass

        active = 0
        seen = []

        def profile(frame, event, target):
            global active
            if getattr(target, '__module__', None) == 'unstd.clone._clone_native':
                if event == 'c_call':
                    active += 1
                elif event in ('c_return', 'c_exception'):
                    active -= 1

        def interrupt(signum, frame):
            seen.append(active > 0)
            raise Interrupted

        def refuse(*args):
            raise AssertionError('exact scalar sets must remain native')

        source = set(range(200000))
        signal.signal(signal.SIGALRM, interrupt)
        sys.setprofile(profile)
        try:
            signal.setitimer(signal.ITIMER_REAL, .001)
            native.clone_walk_graph(source, refuse, refuse, {})
        except Interrupted:
            assert seen == [True], seen
            print('native set interrupted')
        else:
            raise AssertionError('native scalar-set loop ignored the signal')
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            sys.setprofile(None)
        """
    )
    result = run_python(worker, cwd=tmp_path, env=isolated_environment(), timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native set interrupted"
