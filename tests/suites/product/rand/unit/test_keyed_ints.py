"""Explicit per-seed integer draws have a fixed, unbiased, isolated stream."""

from __future__ import annotations

from collections.abc import Sequence
import random

import pytest

from tests.support.environment.process import run_python
from unstd import rand
from unstd.rand import draw, keyed


_MASK = (1 << 64) - 1
_MIN = -(1 << 63)
_MAX = (1 << 63) - 1


def _reference(seed: int, low: int, width: int) -> int:
    """Independent arbitrary-precision reference, including discarded words."""
    for counter in range(1, 1000):
        value = (seed + counter * 0x9E3779B97F4A7C15) % (1 << 64)
        value = ((value ^ (value // (1 << 30))) * 0xBF58476D1CE4E5B9) % (1 << 64)
        value = ((value ^ (value // (1 << 27))) * 0x94D049BB133111EB) % (1 << 64)
        value ^= value // (1 << 31)
        if value >= (1 << 64) % width:
            return low + value % width
    pytest.fail("reference fixture unexpectedly needed 1000 rejection words")


@pytest.fixture(params=[False, True], ids=["python", "native"])
def kernel(request, monkeypatch):
    if request.param and keyed._native_seeded_ints is None:
        pytest.skip("native seeded integer kernel not built")
    if not request.param:
        monkeypatch.setattr(keyed, "_native_seeded_ints", None)


@pytest.mark.usefixtures("kernel")
def test_published_splitmix_first_words_pin_the_stream() -> None:
    # SplitMix64's first word for state zero is the published e220a8397b1dcdaf
    # vector. Subsequent distinct initial states pin the full seed boundary.
    words = [0xE220A8397B1DCDAF, 0x910A2DEC89025CC1, 0x975835DE1C9756CE]
    assert keyed.seeded_ints(3, _MIN, _MAX, True, [0, 1, 2]) == [
        word + _MIN for word in words
    ]


@pytest.mark.usefixtures("kernel")
@pytest.mark.parametrize(
    ("low", "high", "endpoint"),
    [
        (0, 1, False),
        (-2, 3, False),
        (-4, 4, True),
        (_MIN, _MAX, True),
        (_MIN, 1 << 63, False),
        (_MIN, 0, True),  # Half the initial words need rejection on average.
        (5, 5, True),
        (_MAX, 1 << 63, False),
    ],
)
def test_unsigned_wrap_full_width_and_rejection_match_reference(
    low: int, high: int, endpoint: bool
) -> None:
    seeds = [0, 1, 2, 3, 4, 91, 1 << 32, (1 << 63) - 1, 1 << 63, _MASK]
    width = high - low + bool(endpoint)
    expected = [_reference(seed, low, width) for seed in seeds]
    assert keyed.seeded_ints(len(seeds), low, high, endpoint, seeds) == expected


@pytest.mark.usefixtures("kernel")
def test_batch_order_chunking_and_repeated_seeds_do_not_change_results() -> None:
    seeds = [19, 3, 71, 0, 19, _MASK]
    whole = keyed.seeded_ints(6, -17, 91, False, seeds)
    assert keyed.seeded_ints(6, -17, 91, False, seeds[::-1]) == whole[::-1]
    assert (
        keyed.seeded_ints(2, -17, 91, False, seeds[:2])
        + keyed.seeded_ints(4, -17, 91, False, seeds[2:])
    ) == whole
    assert whole[0] == whole[4]


class _Index:
    def __init__(self, value: int) -> None:
        self.value = value
        self.calls = 0

    def __index__(self) -> int:
        self.calls += 1
        return self.value


@pytest.mark.usefixtures("kernel")
def test_seeds_use_index_once_and_snapshot_source_before_hooks() -> None:
    seeds = []

    class MutatingIndex(_Index):
        def __index__(self) -> int:
            seeds.clear()
            return super().__index__()

    first, last = MutatingIndex(7), _Index(13)
    seeds.extend([first, last])
    assert keyed.seeded_ints(2, -3, 9, False, seeds) == [
        _reference(7, -3, 12),
        _reference(13, -3, 12),
    ]
    assert first.calls == last.calls == 1
    assert seeds == []


@pytest.mark.usefixtures("kernel")
@pytest.mark.parametrize("seed", [-1, 1 << 64, 1.5, "1", None, object()])
def test_invalid_seeds_fail_before_any_ordinary_draw(seed) -> None:
    subject = rand.Generator(91)
    state = subject._py.getstate()
    with pytest.raises((ValueError, TypeError)):
        keyed.seeded_ints(3, 0, 4, False, [0, seed, 3])
    assert subject._py.getstate() == state


@pytest.mark.usefixtures("kernel")
@pytest.mark.parametrize(
    ("n", "low", "high", "endpoint", "seeds", "error"),
    [
        (-1, 0, 4, False, [], ValueError),
        (1.5, 0, 4, False, [], TypeError),
        (0, 0.5, 4, False, [], TypeError),
        (0, 0, 4.5, False, [], TypeError),
        (1, 0, 4, False, [], ValueError),
        (0, 0, 4, False, [1], ValueError),
        (0, 0, 0, False, [], ValueError),
        (0, 3, 2, True, [], ValueError),
        (0, _MIN - 1, 0, False, [], ValueError),
        (0, 0, _MAX + 1, True, [], ValueError),
        (0, 1 << 63, 1 << 63, True, [], ValueError),
        (0, _MIN, _MIN, False, [], ValueError),
    ],
)
def test_invalid_empty_batches_still_validate_count_and_bounds(
    n, low, high, endpoint, seeds, error
) -> None:
    with pytest.raises(error):
        keyed.seeded_ints(n, low, high, endpoint, seeds)


@pytest.mark.usefixtures("kernel")
def test_sequence_is_materialized_once() -> None:
    class Seeds(Sequence):
        calls = 0

        def __len__(self):
            return 3

        def __getitem__(self, position):
            self.calls += 1
            return [0, 1, 2][position]

    seeds = Seeds()
    assert keyed.seeded_ints(3, 0, 4, False, seeds) == [3, 1, 2]
    assert seeds.calls == 4  # Three values and the terminating IndexError.


@pytest.mark.parametrize("backend", ["stdlib", "numpy"])
def test_public_opt_in_preserves_all_ordinary_engine_states(backend) -> None:
    if backend == "numpy" and not draw.HAVE_NUMPY:
        pytest.skip("NumPy not installed")
    subject = rand.Generator(91, backend=backend)
    twin = rand.Generator(91, backend=backend)
    global_state = random.getstate()
    expected = [_reference(seed, -3, 12) for seed in [0, 1, 7]]
    result = subject.ints(3, -3, 9, seeds=[0, 1, 7])
    assert list(result) == expected
    assert subject.random() == twin.random()
    assert list(subject.floats(5)) == list(twin.floats(5))
    assert list(subject.ints(7, 0, 9)) == list(twin.ints(7, 0, 9))
    assert random.getstate() == global_state
    if backend == "numpy":
        import numpy as np

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.dtype(np.int64)
    else:
        assert isinstance(result, list)


def test_module_opt_in_matches_instance_without_changing_module_states() -> None:
    rand.seed(65)
    py_state = draw._py.getstate()
    numpy_state = draw._gen.bit_generator.state if draw._gen is not None else None
    assert list(rand.ints(3, 1, 5, seeds=[0, 1, 2])) == [4, 2, 3]
    assert draw._py.getstate() == py_state
    assert (
        draw._gen.bit_generator.state if draw._gen is not None else None
    ) == numpy_state


@pytest.mark.skipif(
    keyed._native_seeded_ints is None, reason="native seeded integer kernel not built"
)
@pytest.mark.parametrize(
    ("seeds", "low", "width"),
    [([], 0, 0), ([], 0, -1), ([], 0, (1 << 64) + 1), ([], 1, 1 << 64)],
)
def test_private_native_kernel_guards_direct_invalid_bounds(seeds, low, width) -> None:
    with pytest.raises((ValueError, OverflowError)):
        keyed._native_seeded_ints(seeds, low, width)


@pytest.mark.skipif(
    keyed._native_seeded_ints is None, reason="native seeded integer kernel not built"
)
def test_private_native_kernel_holds_snapshot_through_mutating_index_hook() -> None:
    seeds = []

    class MutatingIndex:
        def __index__(self):
            seeds.clear()
            return 0

    seeds.extend([MutatingIndex(), 1, 2])
    assert keyed._native_seeded_ints(seeds, 0, 4) == [3, 1, 2]
    assert seeds == []


@pytest.mark.skipif(
    keyed._native_seeded_ints is None, reason="native seeded integer kernel not built"
)
def test_native_partial_output_is_hidden_from_signal_gc_inspection(tmp_path) -> None:
    code = """
import gc
import signal
from unstd.rand import _rand_native
if not hasattr(signal, 'setitimer') or not hasattr(_rand_native, 'seeded_ints'):
    print('UNAVAILABLE')
else:
    observed = []
    def inspect(signum, frame):
        signal.setitimer(signal.ITIMER_REAL, 0)
        gc.collect()
        # A tracked list with uninitialized slots is unsafe to inspect here.
        for value in gc.get_objects():
            if type(value) is list:
                tuple(value)
        observed.append(True)
    class ArmTimer:
        def __index__(self):
            # The last validation hook schedules inspection during generation.
            signal.setitimer(signal.ITIMER_REAL, 0.00001)
            return 1
    signal.signal(signal.SIGALRM, inspect)
    attempts = []
    # A short native batch can finish before macOS delivers its timer. Increase
    # only when no signal was observed; every delivered signal still scans live
    # GC objects while the native call services its cancellation checkpoints.
    for count in (250000, 1000000, 4000000):
        try:
            result = _rand_native.seeded_ints([0] * count + [ArmTimer()], 0, 4)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        assert len(result) == count + 1, ('incomplete native output', count, len(result))
        assert gc.is_tracked(result), ('published output is not tracked', count)
        attempts.append(count)
        if observed:
            break
        del result
    assert observed, ('signal was not delivered during any bounded batch', attempts)
    print('INSPECTED')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout in ("INSPECTED\n", "UNAVAILABLE\n")
