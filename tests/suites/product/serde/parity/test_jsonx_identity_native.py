"""Owned native identity snapshots retain GC, mutation and recursion safety."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import textwrap

import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
pytestmark = pytest.mark.skipif(
    not hasattr(native, "identity_snapshot"),
    reason="native identity snapshot unavailable",
)
_FORCE_GC = os.environ.get("UNSTD_JSONX_IDENTITY_GC_STRESS") == "1"
_FORCED_ONLY = pytest.mark.skipif(
    not _FORCE_GC, reason="requires the isolated forced-GC audit build"
)
_FAULT_ONLY = pytest.mark.skipif(
    not hasattr(native, "_identity_fail_after"),
    reason="requires the isolated allocation-failure audit build",
)


@pytest.fixture(autouse=True)
def _refresh_walker_monitoring():
    # Enter the ordinary walker once to refresh any stale instrumentation left
    # by a prior profiler, as the public fallback does before acceleration.
    jsonx._identity_snapshot(None, set())


def _collected(operation, callback):
    gc.collect()
    thresholds = gc.get_threshold()
    gc.callbacks.append(callback)
    # The audit build explicitly collects at every allocation/copy point.
    # Avoid a threshold-one automatic collection storm in callback bookkeeping.
    gc.set_threshold(1_000_000_000, 1_000_000_000, 1_000_000_000)
    try:
        return operation()
    finally:
        gc.callbacks.remove(callback)
        gc.set_threshold(*thresholds)


def _source_ids(source):
    found = set()
    pending = [source]
    while pending:
        current = pending.pop()
        if type(current) not in (list, dict) or id(current) in found:
            continue
        found.add(id(current))
        pending.extend(current if type(current) is list else current.values())
    return found


def _isolated(source):
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and literal audit source
        [sys.executable, "-c", textwrap.dedent(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _visible_marker_copies(marker, identities):
    # A mistakenly tracked collector can expose otherwise untracked copies.
    # Follow exact list/dict edges from every tracked container, rather than
    # looking only for output dicts listed directly by gc.get_objects().
    roots = gc.get_objects()
    pending = [obj for obj in roots if type(obj) is list or type(obj) is dict]
    observer_ids = (id(roots), id(pending))
    visited = set()
    found = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in observer_ids or identity in identities or identity in visited:
            continue
        visited.add(identity)
        if type(current) is dict:
            for key, value in current.items():
                if type(key) is str and key == "__identity_marker" and value is marker:
                    found.add(identity)
                if type(value) is list or type(value) is dict:
                    pending.append(value)
        else:
            pending.extend(
                value for value in current if type(value) is list or type(value) is dict
            )
    return len(found)


def test_owned_and_shared():
    # Earlier tracing tests may leave conservative deoptimization metadata on
    # 3.12/3.13. Prove acceleration and ownership in a pristine interpreter.
    _isolated(r"""
        import math
        from unstd.serde import _json_native as native
        from unstd.serde import jsonx
        jsonx._identity_snapshot(None, set())
        branch = {"text": ["café😀", -0.0, 2**100]}
        source = [branch, branch]
        result = native.identity_snapshot(source)
        assert result == source
        assert result is not source
        assert result[0] is not result[1]
        assert result[0] is not branch
        assert result[0]["text"] is not branch["text"]
        branch["text"].append("changed")
        assert result == [{"text": ["café😀", -0.0, 2**100]}] * 2
        for bad in ((1,), object(), math.nan, math.inf, "\ud800", {"\ud800": 1}, {1: 2}):
            assert native.identity_snapshot(bad) is NotImplemented
        cycle = []
        cycle.append(cycle)
        assert native.identity_snapshot(cycle) is NotImplemented
        value = 0
        for _ in range(16):
            value = [value]
        assert native.identity_snapshot(value) == value
        assert native.identity_snapshot([value]) is NotImplemented
    """)


@_FORCED_ONLY
def test_whole_operation_privacy_and_reentrancy():
    count = 8
    marker = "private-identity-marker-" + str(id(object()))
    source = [
        {"__identity_marker": marker, "data": [index, {"inner": [index]}]}
        for index in range(count)
    ]
    identities = _source_ids(source)
    seen = []
    failures = []
    reentered = []

    def callback(phase, info):
        if phase != "start":
            return
        try:
            copies = _visible_marker_copies(marker, identities)
            seen.append(copies)
            if copies not in (0, count):
                failures.append(copies)
            nested = jsonx.canonical_strict({"reentrant": [1, {"text": "safe"}]})
            if nested != b'{"reentrant":[1,{"text":"safe"}]}':
                failures.append("reentrant")
            reentered.append(True)
        except BaseException as error:
            failures.append(repr(error))

    result = _collected(lambda: native.identity_snapshot(source), callback)
    assert result == source
    assert not failures, failures
    assert len(seen) > 10
    assert reentered
    assert set(seen) <= {0, count}, seen


@_FORCED_ONLY
def test_declined_tree_never_publishes_completed_sibling_copies():
    marker = "declined-identity-marker-" + str(id(object()))
    source = [
        {"__identity_marker": marker, "data": [index, {"inner": [index]}]}
        for index in range(8)
    ]
    source.append(object())
    identities = _source_ids(source)
    seen = []

    def callback(phase, info):
        if phase == "start":
            seen.append(_visible_marker_copies(marker, identities))

    assert (
        _collected(lambda: native.identity_snapshot(source), callback) is NotImplemented
    )
    assert len(seen) > 10
    assert set(seen) == {0}, seen


@_FORCED_ONLY
def test_list_current_item_is_pinned_during_source_clear():
    source = [[index, {"next": [index]}] for index in range(64)]
    first = json.loads(json.dumps(source[0]))
    iterator_type = type(iter([]))
    fired = []

    def callback(phase, info):
        if phase != "start" or fired:
            return
        for obj in gc.get_objects():
            if type(obj) is iterator_type and any(
                ref is source for ref in gc.get_referents(obj)
            ):
                state = obj.__reduce__()
                if len(state) == 3 and state[2] >= 1:
                    fired.append(True)
                    source.clear()
                    break

    result = _collected(lambda: native.identity_snapshot(source), callback)
    assert fired
    assert result == [first], result


@_FORCED_ONLY
def test_dictionary_current_pair_is_pinned_during_source_clear():
    value = [1, {"next": [2]}]
    source = {"first": value, "second": [3]}
    iterator_type = type(iter({}.items()))
    fired = []

    def callback(phase, info):
        if phase != "start" or fired:
            return
        for obj in gc.get_objects():
            if type(obj) is not iterator_type:
                continue
            refs = gc.get_referents(obj)
            if any(ref is source for ref in refs) and any(
                type(ref) is tuple and len(ref) == 2 and ref[1] is value for ref in refs
            ):
                fired.append(True)
                source.clear()
                break

    with pytest.raises(RuntimeError, match="dictionary changed size"):
        _collected(lambda: native.identity_snapshot(source), callback)
    assert fired


def test_encoder_observes_owned_snapshot():
    source = {"values": [1]}
    original = jsonx._IDENTITY.encode
    received = []

    def encoder(snapshot):
        received.append(snapshot)
        source["values"].append(2)
        assert snapshot is not source
        assert snapshot["values"] is not source["values"]
        return original(snapshot)

    jsonx._IDENTITY.encode = encoder
    try:
        assert jsonx.canonical_strict(source) == b'{"values":[1]}'
    finally:
        jsonx._IDENTITY.encode = original
    assert len(received) == 1
    assert source == {"values": [1, 2]}


@_FORCED_ONLY
def test_gc_injected_unsupported_values_are_declined():
    source = [[1], {"second": [2]}]
    iterator_type = type(iter([]))
    fired = []
    unsupported = object()

    def callback(phase, info):
        if phase != "start" or fired:
            return
        for obj in gc.get_objects():
            if type(obj) is iterator_type and any(
                ref is source for ref in gc.get_referents(obj)
            ):
                state = obj.__reduce__()
                if len(state) == 3 and state[2] >= 1:
                    fired.append(True)
                    source[0][0] = unsupported
                    break

    result = _collected(lambda: native.identity_snapshot(source), callback)
    assert fired
    assert result is NotImplemented


@_FAULT_ONLY
def test_each_owned_allocation_failure_preserves_errors_and_privacy():
    _isolated(r"""
        import gc
        import sys
        from unstd.serde import jsonx
        from unstd.serde import _json_native as native
        from tests.suites.product.serde.parity.test_jsonx_identity_native import (
            _source_ids, _visible_marker_copies,
        )
        jsonx._identity_snapshot(None, set())
        marker = 'allocation-failure-private-' + str(id(object()))
        branch = {'__identity_marker': marker, 'data': [1, {'inner': [2]}]}
        source = [branch, branch]
        identities = _source_ids(source)
        references = tuple(sys.getrefcount(value) for value in (source, branch, marker))
        visible = []
        def observe(phase, info):
            if phase == 'start':
                visible.append(_visible_marker_copies(marker, identities))
        thresholds = gc.get_threshold()
        gc.set_threshold(1000000000, 1000000000, 1000000000)
        gc.collect()
        gc.callbacks.append(observe)
        failed = 0
        try:
            for position in range(256):
                visible.clear()
                native._identity_fail_after(position)
                try:
                    result = native.identity_snapshot(source)
                except MemoryError:
                    failed += 1
                    assert visible and not any(visible), (position, visible)
                else:
                    assert result == source and result is not source
                    assert result[0] is not result[1]
                    del result
                    break
                finally:
                    native._identity_fail_after(-1)
                assert tuple(sys.getrefcount(value) for value in (source, branch, marker)) == references
            else:
                raise AssertionError('allocation sweep did not reach success')
        finally:
            native._identity_fail_after(-1)
            gc.callbacks.remove(observe)
            gc.set_threshold(*thresholds)
        assert failed >= 20, failed
        assert tuple(sys.getrefcount(value) for value in (source, branch, marker)) == references
        print('identity copy failure positions:', failed)
    """)


@_FAULT_ONLY
def test_allocation_error_survives_a_simultaneous_walker_change():
    _isolated(r"""
        import gc
        from unstd.serde import jsonx
        from unstd.serde import _json_native as native
        walker = jsonx._identity_snapshot
        walker(None, set())
        calls = []
        def replacement(*args):
            calls.append(True)
            return None
        def mutate(phase, info):
            if phase == 'start':
                jsonx._identity_snapshot = replacement
        thresholds = gc.get_threshold()
        gc.collect()
        gc.set_threshold(1000000000, 1000000000, 1000000000)
        gc.callbacks.append(mutate)
        native._identity_fail_after(0)
        try:
            try:
                jsonx.canonical_strict([1])
            except MemoryError:
                pass
            else:
                raise AssertionError('MemoryError was replaced by fallback')
        finally:
            native._identity_fail_after(-1)
            gc.callbacks.remove(mutate)
            jsonx._identity_snapshot = walker
            gc.set_threshold(*thresholds)
        assert not calls
    """)


@_FAULT_ONLY
def test_configuration_allocation_failures_release_captured_references():
    _isolated(r"""
        import builtins
        import gc
        import importlib.util
        import math
        import sys
        import types
        from unstd.serde import jsonx
        from unstd.serde import _json_native as original
        failures = 0
        gc.set_threshold(1000000000, 1000000000, 1000000000)
        for position in range(32):
            name = 'identity_failure_' + str(position) + '._json_native'
            spec = importlib.util.spec_from_file_location(name, original.__file__)
            alias = importlib.util.module_from_spec(spec)
            sys.modules[name] = alias
            spec.loader.exec_module(alias)
            namespace = {'__builtins__': builtins.__dict__, 'isfinite': math.isfinite}
            code = jsonx._identity_snapshot.__code__.replace()
            walker = types.FunctionType(code, namespace)
            namespace['_identity_snapshot'] = walker
            references = (sys.getrefcount(walker), sys.getrefcount(code))
            alias._identity_fail_after(position)
            try:
                alias.configure(namespace)
            except MemoryError:
                failures += 1
                assert (sys.getrefcount(walker), sys.getrefcount(code)) == references
                alias._identity_fail_after(-1)
                alias.configure(namespace)
                assert alias.identity_snapshot([1]) is NotImplemented
            else:
                alias._identity_fail_after(-1)
                assert alias.identity_snapshot([1]) == [1]
                break
            finally:
                alias._identity_fail_after(-1)
        else:
            raise AssertionError('configuration sweep did not reach success')
        assert failures == 13, failures
        print('identity configuration failure positions:', failures)
    """)


def test_accepted_and_refused_calls_release_owned_references():
    _isolated("""
        import os
        import sys
        from unstd.serde import _json_native as native
        from unstd.serde import jsonx
        jsonx._identity_snapshot(None, set())
        atom = "identity reference atom " * 37
        source = [{"key": [atom, 2**100]}]
        refused = [source, object()]
        tracked = (atom, source, source[0], source[0]["key"], refused, dict.items)
        def counts():
            return tuple(sys.getrefcount(value) for value in tracked)
        before = counts()
        force = os.environ.get("UNSTD_JSONX_IDENTITY_GC_STRESS") == "1"
        for _ in range(3 if force else 100):
            result = native.identity_snapshot(source)
            assert result == source
            del result
            assert native.identity_snapshot(refused) is NotImplemented
        assert counts() == before, (before, counts())
    """)


def test_replaced_python_walker_owns_the_public_fallback(monkeypatch):
    calls = []

    def replacement(value, active):
        calls.append(value)
        return {"replacement": True}

    source = {"value": [1]}
    monkeypatch.setattr(jsonx, "_identity_snapshot", replacement)
    assert native.identity_snapshot(source) is NotImplemented
    assert jsonx.canonical_strict(source) == b'{"replacement":true}'
    assert calls == [source]


def test_changed_isfinite_uses_the_current_python_callable(monkeypatch):
    calls = []

    def changed(value):
        calls.append(value)
        return False

    monkeypatch.setattr(jsonx, "isfinite", changed)
    assert native.identity_snapshot({"value": 1.5}) is NotImplemented
    with pytest.raises(ValueError, match="finite"):
        jsonx.canonical_strict({"value": 1.5})
    assert calls == [1.5]


@pytest.mark.parametrize(
    "name",
    ["type", "str", "int", "bool", "float", "list", "dict", "id", "isinstance", "set"],
)
def test_global_builtin_shadows_decline_without_calling_them(name, monkeypatch):
    def forbidden(*args):
        pytest.fail("native identity admission called a shadowed builtin")

    monkeypatch.setattr(jsonx, name, forbidden, raising=False)
    assert native.identity_snapshot({"value": [1]}) is NotImplemented


def test_changes_in_the_actual_builtin_map_are_not_hidden_by_globals():
    _isolated("""
        import builtins
        from unstd.serde import jsonx
        from unstd.serde import _json_native as native
        jsonx._identity_snapshot(None, set())
        operation = native.identity_snapshot
        source = {"value": [1]}
        mapping = builtins.__dict__
        def changed(*args):
            return None
        for name in ("type", "str", "int", "bool", "float", "list", "dict", "id", "isinstance", "set"):
            original = mapping[name]
            try:
                mapping[name] = changed
                result = operation(source)
            finally:
                mapping[name] = original
            assert result is NotImplemented, name
        assert operation(source) == source
    """)


@pytest.mark.parametrize("mutation", ["builtins", "isfinite", "shadow"])
def test_initial_mutated_configuration_cannot_be_blessed_by_retry(mutation):
    _isolated(f"""
        import builtins
        import importlib.util
        import math
        import sys
        import types
        from unstd.serde import jsonx
        from unstd.serde import _json_native as original
        spec = importlib.util.spec_from_file_location("identity_audit._json_native", original.__file__)
        alias = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = alias
        spec.loader.exec_module(alias)
        namespace = {{"__builtins__": builtins.__dict__, "isfinite": math.isfinite}}
        code = jsonx._identity_snapshot.__code__
        def changed(*args):
            return None
        mutation = {mutation!r}
        if mutation == "builtins":
            altered = dict(builtins.__dict__, id=changed)
            namespace["__builtins__"] = altered
            namespace["_identity_snapshot"] = types.FunctionType(code, namespace)
            namespace["__builtins__"] = builtins.__dict__
        else:
            namespace["_identity_snapshot"] = types.FunctionType(code, namespace)
            if mutation == "isfinite":
                namespace["isfinite"] = changed
            else:
                namespace["type"] = changed
        alias.configure(namespace)
        assert alias.identity_snapshot([1]) is NotImplemented
        namespace.pop("type", None)
        namespace["isfinite"] = math.isfinite
        namespace["_identity_snapshot"] = types.FunctionType(code, namespace)
        alias.configure(namespace)
        assert alias.identity_snapshot([1]) is NotImplemented
    """)


@_FORCED_ONLY
def test_configuration_pins_function_and_code_before_allocating_metadata():
    _isolated("""
        import builtins
        import gc
        import importlib.util
        import math
        import sys
        import types
        from unstd.serde import jsonx
        from unstd.serde import _json_native as original
        spec = importlib.util.spec_from_file_location("identity_capture_audit._json_native", original.__file__)
        alias = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = alias
        spec.loader.exec_module(alias)
        namespace = {"__builtins__": builtins.__dict__, "isfinite": math.isfinite}
        namespace["_identity_snapshot"] = types.FunctionType(jsonx._identity_snapshot.__code__.replace(), namespace)
        fired = []
        def mutate(phase, info):
            if phase == "start" and not fired:
                fired.append(True)
                namespace["_identity_snapshot"] = object()
                alias.configure(namespace)
        thresholds = gc.get_threshold()
        gc.collect()
        gc.callbacks.append(mutate)
        gc.set_threshold(1000000000, 1000000000, 1000000000)
        try:
            alias.configure(namespace)
        finally:
            gc.callbacks.remove(mutate)
            gc.set_threshold(*thresholds)
        assert fired
        assert alias.identity_snapshot([1]) is NotImplemented
        namespace["_identity_snapshot"] = types.FunctionType(jsonx._identity_snapshot.__code__, namespace)
        alias.configure(namespace)
        assert alias.identity_snapshot([1]) is NotImplemented
    """)


def test_namespace_collision_keys_decline_without_hashing_or_equality():
    namespace = jsonx.__dict__
    name = next(
        key for key in namespace if type(key) is str and key == "_identity_snapshot"
    )
    walker = namespace[name]
    armed = []
    observed = []

    class Collision:
        def __hash__(self):
            observed.append("hash")
            return hash(name)

        def __eq__(self, other):
            observed.append("equality")
            if armed:
                pytest.fail("native identity guard evaluated a foreign key")
            return False

    key = Collision()
    try:
        namespace.pop(name)
        namespace[key] = object()
        namespace[name] = walker
        armed.append(True)
        before = observed.copy()
        assert native.identity_snapshot({"value": [1]}) is NotImplemented
        assert observed == before
    finally:
        armed.clear()
        del namespace[key]
        namespace[name] = walker


@pytest.mark.parametrize("attribute", ["__defaults__", "__kwdefaults__", "__code__"])
def test_modified_walker_metadata_defers_to_python(attribute, monkeypatch):
    walker = jsonx._identity_snapshot

    def replacement(value, active):
        return {"replacement": True}

    changed = {
        "__defaults__": (None, None),
        "__kwdefaults__": {"unexpected": 1},
        "__code__": replacement.__code__,
    }[attribute]
    monkeypatch.setattr(walker, attribute, changed)
    assert native.identity_snapshot({"value": [1]}) is NotImplemented
    expected = b'{"replacement":true}' if attribute == "__code__" else b'{"value":[1]}'
    assert jsonx.canonical_strict({"value": [1]}) == expected


@_FORCED_ONLY
def test_walker_replaced_during_copy_discards_the_owned_result(monkeypatch):
    source = [{"value": [1]}]
    fired = []

    def replacement(value, active):
        return {"replacement": True}

    def callback(phase, info):
        if phase == "start" and not fired:
            fired.append(True)
            monkeypatch.setattr(jsonx, "_identity_snapshot", replacement)

    assert (
        _collected(lambda: native.identity_snapshot(source), callback) is NotImplemented
    )
    assert fired
    assert jsonx.canonical_strict(source) == b'{"replacement":true}'


@_FORCED_ONLY
def test_walker_change_does_not_replace_an_inflight_dictionary_error(monkeypatch):
    source = {"first": [1, {"nested": [2]}], "second": [3]}
    first_identity = id(source["first"])
    iterator_type = type(iter({}.items()))
    fired = []
    calls = []

    def replacement(value, active):
        calls.append(value)
        return {"replacement": True}

    def callback(phase, info):
        if phase != "start" or fired:
            return
        for obj in gc.get_objects():
            if type(obj) is not iterator_type:
                continue
            references = gc.get_referents(obj)
            if any(ref is source for ref in references) and any(
                type(ref) is tuple and len(ref) == 2 and id(ref[1]) == first_identity
                for ref in references
            ):
                fired.append(True)
                monkeypatch.setattr(jsonx, "_identity_snapshot", replacement)
                source.clear()
                break

    with pytest.raises(RuntimeError, match="dictionary changed size"):
        _collected(lambda: jsonx.canonical_strict(source), callback)
    assert fired
    assert calls == []


@_FORCED_ONLY
def test_last_leaf_recursion_limit_change_discards_the_owned_result():
    source = [1]
    iterator_type = type(iter([]))
    visits = []
    fired = []
    previous_limit = sys.getrecursionlimit()

    def callback(phase, info):
        if phase != "start" or fired:
            return
        for obj in gc.get_objects():
            if type(obj) is not iterator_type or not any(
                ref is source for ref in gc.get_referents(obj)
            ):
                continue
            state = obj.__reduce__()
            if len(state) != 3 or state[2] != 1:
                continue
            visits.append(True)
            if len(visits) < 2:
                continue
            frame = sys._getframe()
            depth = 0
            while frame is not None:
                depth += 1
                frame = frame.f_back
            sys.setrecursionlimit(depth + 24)
            fired.append(True)
            break

    try:
        result = _collected(lambda: native.identity_snapshot(source), callback)
    finally:
        sys.setrecursionlimit(previous_limit)
    assert fired
    assert result is NotImplemented
