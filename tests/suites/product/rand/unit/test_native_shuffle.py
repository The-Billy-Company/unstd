"""Native shuffles preserve streams and own list values across foreign callbacks.

The public stdlib shuffle is the independent output/state oracle. Adversarial
private calls check cleanup, mutation and interruptibility at the C boundary.
"""

from __future__ import annotations

import gc
import random
import sys
import weakref

import pytest

from tests.support.environment.process import run_python


native = pytest.importorskip("unstd.rand._rand_native")


@pytest.mark.parametrize("seed", [0, 1, 173, 2**100 + 19])
@pytest.mark.parametrize("size", [0, 1, 2, 3, 32, 256, 10_000])
def test_native_shuffle_matches_stdlib_values_and_complete_state(seed, size):
    subject = random.Random(seed)  # noqa: S311 — seeded simulation reference
    reference = random.Random(seed)  # noqa: S311 — independent reference engine
    actual, expected = list(range(size)), list(range(size))
    assert native.shuffle(subject.getrandbits, actual) is None
    reference.shuffle(expected)
    assert actual == expected
    assert subject.getstate() == reference.getstate()


def test_large_repeated_shuffle_keeps_live_engine_state_and_item_identity():
    subject = random.Random(211)  # noqa: S311 — seeded simulation reference
    reference = random.Random(211)  # noqa: S311 — independent reference engine
    actual = [object() for _ in range(100_000)]
    expected = actual.copy()
    for _ in range(4):
        native.shuffle(subject.getrandbits, actual)
        reference.shuffle(expected)
        assert all(left is right for left, right in zip(actual, expected, strict=True))
        assert subject.getstate() == reference.getstate()
        assert subject.random() == reference.random()


def test_native_shuffle_uses_stdlib_bit_lengths_and_rejects_out_of_range_draws():
    draws = iter([7, 6, 3, 2, 1])
    widths = []

    def getrandbits(width):
        widths.append(width)
        return next(draws)

    items = list(range(4))
    native.shuffle(getrandbits, items)
    assert widths == [3, 3, 3, 2, 2]
    assert items == list(range(4))
    with pytest.raises(StopIteration):
        next(draws)


@pytest.mark.parametrize("size", [0, 1])
def test_empty_and_singleton_shuffle_do_not_draw(size):
    def forbidden(_width):
        pytest.fail("shuffle drew for fewer than two items")

    items = list(range(size))
    assert native.shuffle(forbidden, items) is None
    assert items == list(range(size))


def test_callback_error_keeps_partial_stdlib_mutation_and_can_be_reused():
    error = RuntimeError("getrandbits failed")

    def operation():
        calls = 0

        def getrandbits(_width):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise error
            return 0

        return getrandbits

    actual, expected = list(range(4)), list(range(4))
    reference = random.Random(223)  # noqa: S311 — reference rejection loop
    reference.getrandbits = operation()
    with pytest.raises(RuntimeError) as caught:
        reference.shuffle(expected)
    assert caught.value is error
    with pytest.raises(RuntimeError) as caught:
        native.shuffle(operation(), actual)
    assert caught.value is error
    assert actual == expected
    native.shuffle(lambda _width: 0, actual)
    assert sorted(actual) == list(range(4))


@pytest.mark.parametrize("mutation", ["clear", "shrink", "grow"])
def test_callback_size_changes_raise_before_native_swap(mutation):
    items = list(range(4))

    def getrandbits(_width):
        if mutation == "clear":
            items.clear()
        elif mutation == "shrink":
            items.pop()
        else:
            items.append(4)
        return 0

    with pytest.raises(RuntimeError, match="list size changed"):
        native.shuffle(getrandbits, items)
    assert (
        items == {"clear": [], "shrink": [0, 1, 2], "grow": [0, 1, 2, 3, 4]}[mutation]
    )


def test_callback_can_replace_all_items_without_dangling_old_references():
    items = [object() for _ in range(8)]
    replacements = [object() for _ in range(8)]
    calls = 0

    def getrandbits(_width):
        nonlocal calls
        if calls == 0:
            items[:] = replacements
        calls += 1
        return 0

    native.shuffle(getrandbits, items)
    expected = replacements.copy()
    reference = random.Random(227)  # noqa: S311 — independent algorithm oracle
    reference.getrandbits = lambda _width: 0
    reference.shuffle(expected)
    assert all(left is right for left, right in zip(items, expected, strict=True))
    assert calls == len(items) - 1


def test_result_finalizer_size_change_is_checked_before_native_swap():
    items = list(range(4))

    class Result(int):
        def __del__(self):
            items.clear()

    def getrandbits(_width):
        return Result(0)

    with pytest.raises(RuntimeError, match="list size changed"):
        native.shuffle(getrandbits, items)
    assert items == []


@pytest.mark.parametrize("result", [-1, 2**200, object(), 0.5])
def test_invalid_private_callback_results_fail_without_touching_items(result):
    items = list(range(4))
    with pytest.raises((OverflowError, TypeError)):
        native.shuffle(lambda _width: result, items)
    assert items == list(range(4))


def test_private_integer_result_validation_does_not_invoke_index_hooks():
    items = list(range(4))

    class Index:
        def __index__(self):
            items.clear()
            return 0

    with pytest.raises(TypeError, match="integer is required"):
        native.shuffle(lambda _width: Index(), items)
    assert items == list(range(4))


def test_profile_callback_can_clear_list_before_native_swap():
    engine = random.Random(229)  # noqa: S311 — live C callback under profiling
    items = list(range(4))
    observed = []

    def getrandbits(width):
        return engine.getrandbits(width)

    def profile(frame, event, _operation):
        if event == "return" and frame.f_code is getrandbits.__code__:
            observed.append(event)
            items.clear()

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        with pytest.raises(RuntimeError, match="list size changed"):
            native.shuffle(getrandbits, items)
    finally:
        sys.setprofile(previous)
    assert observed
    assert items == []


def test_repeated_native_calls_release_items_and_callbacks():
    class Item:
        pass

    class Bits:
        def __call__(self, _width):
            return 0

    def exercise():
        items = [Item() for _ in range(32)]
        references = [weakref.ref(item) for item in items]
        bits = Bits()
        callback_reference = weakref.ref(bits)
        for _ in range(100):
            native.shuffle(bits, items)
        assert all(reference() is not None for reference in references)
        return references, callback_reference

    references, callback_reference = exercise()
    gc.collect()
    assert callback_reference() is None
    assert all(reference() is None for reference in references)


@pytest.mark.parametrize("items", [(), (1, 2), "ab", {1, 2}])
def test_private_kernel_rejects_non_list_sequences(items):
    with pytest.raises(TypeError, match="exact list"):
        native.shuffle(lambda _width: 0, items)


def test_private_kernel_rejects_list_subclasses_and_non_callable_sources():
    class Items(list):
        pass

    with pytest.raises(TypeError, match="exact list"):
        native.shuffle(lambda _width: 0, Items([1, 2]))
    with pytest.raises(TypeError, match="getrandbits must be callable"):
        native.shuffle(None, [])


def test_rejection_loop_checks_pending_signals_in_owned_worker(tmp_path):
    code = """
import signal
from unstd.rand import _rand_native
if not hasattr(signal, 'setitimer'):
    print('UNAVAILABLE')
else:
    interrupted = KeyboardInterrupt('native shuffle cancelled')
    def cancel(signum, frame):
        raise interrupted
    signal.signal(signal.SIGALRM, cancel)
    signal.setitimer(signal.ITIMER_REAL, 0.02)
    try:
        # int(2) always returns the rejected upper bound; every call is in C.
        _rand_native.shuffle(int, [object(), object()])
    except KeyboardInterrupt as error:
        assert error is interrupted
    else:
        raise AssertionError('native rejection loop ignored cancellation')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print('INTERRUPTED')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout in ("INTERRUPTED\n", "UNAVAILABLE\n")


def test_generator_shuffle_uses_native_live_engine_and_retains_module_alias(
    monkeypatch,
):
    from unstd import rand

    subject = rand.Generator(233)
    reference = random.Random(233)  # noqa: S311 — independent native-route oracle
    actual, expected = list(range(256)), list(range(256))
    calls = []
    operation = native.shuffle

    def observe(getrandbits, items):
        calls.append((getrandbits.__self__, items))
        return operation(getrandbits, items)

    monkeypatch.setattr(native, "shuffle", observe)
    assert subject.shuffle(actual) is None
    reference.shuffle(expected)
    assert len(calls) == 1
    assert calls[0][0] is subject._py
    assert calls[0][1] is actual
    assert actual == expected
    assert subject._py.getstate() == reference.getstate()
    assert rand.shuffle.__func__ is random.Random.shuffle


def test_generator_shuffle_without_native_retains_stdlib_stream(monkeypatch):
    from unstd import rand
    from unstd.rand import draw

    monkeypatch.setattr(draw, "_native", None)
    subject = rand.Generator(239)
    reference = random.Random(239)  # noqa: S311 — independent fallback oracle
    actual, expected = list(range(256)), list(range(256))
    subject.shuffle(actual)
    reference.shuffle(expected)
    assert actual == expected
    assert subject._py.getstate() == reference.getstate()


def _forbidden_native(*_args):
    pytest.fail("custom shuffle behavior reached native kernel")


def test_generator_shuffle_keeps_custom_sequence_accessors(monkeypatch):
    from unstd import rand

    monkeypatch.setattr(native, "shuffle", _forbidden_native)
    calls = []

    class Items(list):
        def __getitem__(self, position):
            calls.append(("get", position))
            return super().__getitem__(position)

        def __setitem__(self, position, value):
            calls.append(("set", position))
            super().__setitem__(position, value)

    subject = rand.Generator(241)
    reference = random.Random(241)  # noqa: S311 — independent sequence oracle
    actual, expected = Items(range(32)), list(range(32))
    subject.shuffle(actual)
    reference.shuffle(expected)
    assert actual == expected
    assert {kind for kind, _ in calls} == {"get", "set"}
    assert subject._py.getstate() == reference.getstate()


def test_generator_shuffle_keeps_custom_engine_method(monkeypatch):
    from unstd import rand

    monkeypatch.setattr(native, "shuffle", _forbidden_native)
    calls = []

    class Engine(random.Random):
        def shuffle(self, items):
            calls.append(items)
            super().shuffle(items)

    subject = rand.Generator(251)
    subject._py = Engine(251)
    reference = random.Random(251)  # noqa: S311 — independent engine oracle
    actual, expected = list(range(32)), list(range(32))
    subject.shuffle(actual)
    reference.shuffle(expected)
    assert len(calls) == 1
    assert calls[0] is actual
    assert actual == expected
    assert subject._py.getstate() == reference.getstate()


@pytest.mark.parametrize("method", ["shuffle", "_randbelow", "getrandbits"])
@pytest.mark.parametrize("scope", ["class", "instance"])
def test_generator_shuffle_keeps_replaced_stdlib_methods(monkeypatch, method, scope):
    from types import MethodType

    from unstd import rand

    monkeypatch.setattr(native, "shuffle", _forbidden_native)
    subject = rand.Generator(257)
    reference = random.Random(257)  # noqa: S311 — independent hook oracle
    original = getattr(random.Random, method)
    calls = []

    def observe(self, *args):
        calls.append(self)
        return original(self, *args)

    if scope == "class":
        monkeypatch.setattr(random.Random, method, observe)
    else:
        setattr(subject._py, method, MethodType(observe, subject._py))
    actual, expected = list(range(32)), list(range(32))
    subject.shuffle(actual)
    assert calls
    assert all(engine is subject._py for engine in calls)
    reference.shuffle(expected)
    assert actual == expected
    assert subject._py.getstate() == reference.getstate()
