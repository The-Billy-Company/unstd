"""Virtual clocks distinguish scheduler delays from the measured operation."""

from __future__ import annotations

import gc
from types import SimpleNamespace

import pytest

from bench.core import timing


def test_preempted_tiny_calibration_batch_is_not_an_adequate_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = calls = 0
    cost = 200

    def work() -> None:
        nonlocal elapsed, calls
        calls += 1
        elapsed += cost
        # The warmup succeeds. The first calibration call then loses 9 ms
        # to the scheduler, independently of this operation's 200 ns cost.
        if calls == 2:
            elapsed += 9_000_000

    monkeypatch.setattr(
        timing, "time", SimpleNamespace(perf_counter_ns=lambda: elapsed)
    )
    result = timing.measure(work, rounds=3)
    assert result.iters * cost >= timing._MIN_BATCH_NS
    assert result.per_op_ns == cost
    assert result.samples_ns == (cost, cost, cost)
    assert result.cpu_samples_ns == (None, None, None)


def test_genuinely_slow_operation_keeps_bounded_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = calls = 0
    cost = 300_000_000

    def work() -> None:
        nonlocal elapsed, calls
        calls += 1
        elapsed += cost

    monkeypatch.setattr(
        timing, "time", SimpleNamespace(perf_counter_ns=lambda: elapsed)
    )
    result = timing.measure(work, rounds=11)
    assert result.iters == 1
    assert result.rounds == 2
    assert result.per_op_ns == cost
    assert result.samples_ns == (cost, cost)
    assert calls <= 5


def test_wall_and_cpu_samples_remain_separate_and_retain_all_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches = iter((2_000, 3_000))
    cpu_ticks = iter((100, 600, 800, 1_400))
    events: list[str] = []

    def batch(_fn: object, _iters: int) -> int:
        events.append("batch")
        return next(batches)

    def cpu_clock() -> int:
        events.append("cpu")
        return next(cpu_ticks)

    monkeypatch.setattr(timing, "_calibrate", lambda fn: (10, 1_000))
    monkeypatch.setattr(timing, "_batch", batch)
    monkeypatch.setattr(timing, "time", SimpleNamespace(thread_time_ns=cpu_clock))
    result = timing.measure(lambda: None, rounds=3)

    assert result.samples_ns == (100.0, 200.0, 300.0)
    assert result.cpu_samples_ns == (None, 50.0, 60.0)
    assert result.per_op_ns == 100.0
    assert result.spread == 2.0
    assert events == ["cpu", "batch", "cpu", "cpu", "batch", "cpu"]


def test_cpu_diagnostics_cannot_replace_a_slow_wall_clock_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cpu_ticks = iter((0, 10, 10, 20))
    monkeypatch.setattr(timing, "_calibrate", lambda fn: (10, 10_000))
    monkeypatch.setattr(timing, "_batch", lambda fn, iters: 10_000)
    monkeypatch.setattr(
        timing, "time", SimpleNamespace(thread_time_ns=lambda: next(cpu_ticks))
    )
    result = timing.measure(lambda: None, rounds=3)
    assert result.per_op_ns == 1_000
    assert result.samples_ns == (1_000, 1_000, 1_000)
    assert result.cpu_samples_ns == (None, 1.0, 1.0)


def test_missing_cpu_clock_retains_wall_samples_without_fabricated_cpu_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timing, "_calibrate", lambda fn: (10, 1_000))
    monkeypatch.setattr(timing, "_batch", lambda fn, iters: 2_000)
    monkeypatch.setattr(timing, "time", SimpleNamespace())
    result = timing.measure(lambda: None, rounds=3)
    assert result.samples_ns == (100.0, 200.0, 200.0)
    assert result.cpu_samples_ns == (None, None, None)


def test_cpu_diagnostic_clock_stays_outside_the_operation_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = 0

    def work() -> None:
        nonlocal elapsed
        elapsed += 500

    def unexpected_cpu_clock() -> int:
        pytest.fail("CPU diagnostics entered the wall-clock batch loop")

    monkeypatch.setattr(
        timing,
        "time",
        SimpleNamespace(
            perf_counter_ns=lambda: elapsed, thread_time_ns=unexpected_cpu_clock
        ),
    )
    assert timing._batch(work, 100) == 50_000


@pytest.mark.parametrize("enabled", [False, True])
def test_exception_inside_calibration_restores_callers_gc_policy(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    previous = gc.isenabled()
    calls = 0
    monkeypatch.setattr(timing, "time", SimpleNamespace(perf_counter_ns=lambda: 0))

    def work() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert not gc.isenabled()
            message = "injected calibration failure"
            raise RuntimeError(message)

    try:
        if enabled:
            gc.enable()
        else:
            gc.disable()
        with pytest.raises(RuntimeError, match="injected calibration failure"):
            timing.measure(work, rounds=3)
        assert gc.isenabled() is enabled
    finally:
        if previous:
            gc.enable()
        else:
            gc.disable()
