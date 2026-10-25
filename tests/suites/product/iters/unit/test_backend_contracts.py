"""Backend shortcuts preserve iterator setup, projections and actual consumption.

These cases observe behavior that successful output alone cannot establish:
initializing an input can fail, keyed duplicates still require one projection,
and an iterable's advertised size need not be its consumed size.
"""

from __future__ import annotations

import sys
import weakref

import pytest

from unstd.iters import chunked, ilen, unique_everseen, windowed


@pytest.mark.parametrize("strict", [False, True])
def test_chunked_defers_source_initialization_until_the_first_pull(strict) -> None:
    failure = RuntimeError("source initialization refused")

    class Refusing:
        def __iter__(self):
            raise failure

    produced = chunked(Refusing(), 2, strict=strict)
    with pytest.raises(RuntimeError, match="source initialization refused") as raised:
        next(produced)
    assert raised.value is failure


@pytest.mark.parametrize("strict", [False, True])
def test_chunked_defers_noninteger_size_validation_until_the_first_pull(strict) -> None:
    produced = chunked(range(5), 1.5, strict=strict)
    with pytest.raises(TypeError):
        next(produced)


@pytest.mark.parametrize("step", [1, 2, 10])
def test_zero_width_windows_do_not_initialize_the_source(step) -> None:
    class Untouchable:
        def __iter__(self):
            pytest.fail("zero-width windows initialized the source")

    assert list(windowed(Untouchable(), 0, step)) == [()]


def test_keyed_unique_projects_each_pulled_item_once_across_mixed_histories() -> None:
    values = list(enumerate([1, 1, [], [], 2, 2]))
    calls = []

    def project(row):
        calls.append(row[0])
        return row[1]

    produced = unique_everseen(values, key=project)
    assert calls == []
    assert next(produced) is values[0]
    assert calls == [0]
    assert next(produced) is values[2]
    assert calls == [0, 1, 2]
    assert next(produced) is values[4]
    assert calls == [0, 1, 2, 3, 4]
    assert list(produced) == []
    assert calls == list(range(6))


def test_ilen_counts_actual_pulls_without_consulting_size_advertisements() -> None:
    class Misleading:
        def __init__(self):
            self.source = iter(range(7))
            self.pulled = 0

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self.source)
            self.pulled += 1
            return item

        def __len__(self):
            pytest.fail("counting consulted an unreliable length")

        def __length_hint__(self):
            pytest.fail("counting consulted an unreliable length hint")

    source = Misleading()
    assert ilen(source) == 7
    assert source.pulled == 7
    assert next(source, None) is None


class Index:
    """An integer-index provider without arithmetic operators."""

    def __init__(self, value):
        self.value = value

    def __index__(self):
        return self.value

    def __lt__(self, other):
        return self.value < other

    def __gt__(self, other):
        return self.value > other

    def __eq__(self, other):
        return self.value == other


def test_windowed_accepts_index_providers_without_arithmetic_operators() -> None:
    assert list(windowed(range(5), Index(3))) == [(0, 1, 2), (1, 2, 3), (2, 3, 4)]
    assert list(windowed(range(8), 2, Index(3))) == [(0, 1), (3, 4), (6, 7)]


@pytest.mark.parametrize("n", [1.0, 2.0, 3.0])
def test_windowed_rejects_noninteger_width_without_initializing_the_source(n) -> None:
    class Untouchable:
        def __iter__(self):
            pytest.fail("invalid window width initialized the source")

    produced = windowed(Untouchable(), n)
    with pytest.raises(TypeError):
        next(produced)


def test_windowed_width_overflow_precedes_source_initialization() -> None:
    class Untouchable:
        def __iter__(self):
            pytest.fail("oversized window initialized the source")

    produced = windowed(Untouchable(), sys.maxsize + 1)
    with pytest.raises(OverflowError):
        next(produced)


def test_windowed_step_overflow_precedes_any_stride_reads() -> None:
    class Counted:
        def __init__(self):
            self.source = iter(range(5))
            self.pulled = 0

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self.source)
            self.pulled += 1
            return item

    source = Counted()
    produced = windowed(source, 2, sys.maxsize + 1)
    assert next(produced) == (0, 1)
    assert source.pulled == 2
    with pytest.raises(ValueError, match="islice"):
        next(produced)
    assert source.pulled == 2


@pytest.mark.parametrize("step", [2.0, 2.5])
def test_windowed_noninteger_stride_errors_follow_the_initial_window(step) -> None:
    source = iter(range(5))
    produced = windowed(source, 2, step)
    assert next(produced) == (0, 1)
    with pytest.raises(ValueError, match="islice"):
        next(produced)
    assert list(source) == [2, 3, 4]


@pytest.mark.parametrize(("n", "step"), [(2, 3), (4, 6)])
def test_gapped_windows_release_evicted_items_before_consuming_more(n, step) -> None:
    class Token:
        pass

    live = weakref.WeakSet()
    peaks = []

    def source():
        for _ in range(100):
            token = Token()
            live.add(token)
            peaks.append(len(live))
            yield token
            del token

    assert sum(map(len, windowed(source(), n, step))) > 0
    assert max(peaks) == n + 1
    assert len(live) == 0
