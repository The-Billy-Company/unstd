"""Shared time primitives retain the native callables and strict decode errors."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as civil_time
import inspect
import time

import pytest

from unstd.time import timeutil


@pytest.mark.parametrize(
    ("name", "native"),
    [
        ("wall_ns", "time_ns"),
        ("perf_ns", "perf_counter_ns"),
        ("process_time", "process_time"),
        ("process_time_ns", "process_time_ns"),
        ("thread_time", "thread_time"),
        ("thread_time_ns", "thread_time_ns"),
        ("sleep", "sleep"),
    ],
)
def test_clocks_and_sleep_are_the_native_callables(name: str, native: str) -> None:
    """Identity proves there is no wrapper on successful or failing calls."""
    expected = getattr(time, native, None)
    if expected is None:
        assert not hasattr(timeutil, name)
        assert name not in timeutil.__all__
    else:
        assert getattr(timeutil, name) is expected
        assert name in timeutil.__all__


@pytest.mark.parametrize(
    ("name", "owner", "method"),
    [
        ("now", datetime, "now"),
        ("date_today", date, "today"),
        ("datetime_today", datetime, "today"),
        ("from_iso", datetime, "fromisoformat"),
        ("date_from_iso", date, "fromisoformat"),
        ("time_from_iso", civil_time, "fromisoformat"),
        ("from_timestamp", datetime, "fromtimestamp"),
    ],
)
def test_datetime_helpers_are_prebound_native_methods(name, owner, method) -> None:
    alias = getattr(timeutil, name)
    assert inspect.isbuiltin(alias)
    assert alias.__self__ is owner
    assert alias == getattr(owner, method)
    assert not hasattr(alias, "__code__")


@pytest.mark.parametrize(
    ("name", "native", "raw"),
    [
        ("from_iso", datetime.fromisoformat, "2026-01-01T03:04:05Z"),
        ("from_iso", datetime.fromisoformat, "20260101T030405"),
        ("from_iso", datetime.fromisoformat, "2026-W01-4T03:04:05+05:45"),
        ("from_iso", datetime.fromisoformat, "2026-01-01T03:04:05.123456-08:00"),
        ("from_iso", datetime.fromisoformat, "2026-01-01"),
        ("date_from_iso", date.fromisoformat, "2026-01-01"),
        ("date_from_iso", date.fromisoformat, "20260101"),
        ("date_from_iso", date.fromisoformat, "2026-W01-4"),
        ("time_from_iso", civil_time.fromisoformat, "03:04:05.123456"),
        ("time_from_iso", civil_time.fromisoformat, "T03:04:05Z"),
        ("time_from_iso", civil_time.fromisoformat, "030405+05:45"),
    ],
)
def test_strict_decoders_preserve_supported_shapes(name, native, raw) -> None:
    expected = native(raw)
    actual = getattr(timeutil, name)(raw)
    assert type(actual) is type(expected)
    assert actual == expected
    assert actual.isoformat() == expected.isoformat()


@pytest.mark.parametrize(
    ("name", "owner"),
    [("from_iso", datetime), ("date_from_iso", date), ("time_from_iso", civil_time)],
)
@pytest.mark.parametrize(
    "raw", ["", "not an instant", "2026-13-45T24:99:99", None, b"2026-01-01", 0]
)
def test_strict_decoders_preserve_exception_type_and_message(name, owner, raw) -> None:
    with pytest.raises(
        TypeError if not isinstance(raw, str) else ValueError
    ) as expected:
        owner.fromisoformat(raw)
    with pytest.raises(type(expected.value)) as actual:
        getattr(timeutil, name)(raw)
    assert actual.value.args == expected.value.args


def test_naive_and_zoned_now_keep_the_native_semantics() -> None:
    before = datetime.now()  # noqa: DTZ005 - the native local-naive contract is under test.
    observed = timeutil.now()
    after = datetime.now()  # noqa: DTZ005 - bounds use the same native local-naive contract.
    assert observed.tzinfo is None
    assert before <= observed <= after
    before_utc = datetime.now(UTC)
    zoned = timeutil.now(UTC)
    assert zoned.tzinfo is UTC
    assert before_utc <= zoned <= datetime.now(UTC)


def test_cpu_clocks_measure_the_native_cpu_clock() -> None:
    before = time.process_time_ns()
    observed = timeutil.process_time_ns()
    assert before <= observed <= time.process_time_ns()
    assert abs(timeutil.process_time() - observed / 1e9) < 1


def test_nanosecond_clocks_keep_integer_precision() -> None:
    assert type(timeutil.wall_ns()) is int
    assert type(timeutil.perf_ns()) is int
    assert abs(timeutil.wall_ns() / 1e9 - time.time()) < 1


@pytest.mark.parametrize(
    "seconds", [-1_793_515_500.125, -1.5, 0, 1.5, 1_793_515_500.125]
)
@pytest.mark.parametrize("zone", [None, UTC])
def test_native_epoch_decoding_retains_naive_and_explicit_zones(seconds, zone) -> None:
    expected = datetime.fromtimestamp(seconds, zone)
    actual = timeutil.from_timestamp(seconds, zone)
    assert actual == expected
    assert actual.tzinfo is expected.tzinfo
    assert actual.fold == expected.fold
    assert actual.microsecond == expected.microsecond


def test_sleep_keeps_the_native_rejection() -> None:
    with pytest.raises(ValueError, match="non-negative") as expected:
        time.sleep(-1)
    with pytest.raises(ValueError, match="non-negative") as actual:
        timeutil.sleep(-1)
    assert actual.value.args == expected.value.args


def test_strict_and_permissive_parsing_remain_distinct() -> None:
    raw = "invalid"
    assert timeutil.parse_iso(raw) is None
    with pytest.raises(ValueError, match="isoformat"):
        timeutil.from_iso(raw)
    assert timeutil.parse_date(raw) is None
    with pytest.raises(ValueError, match="isoformat"):
        timeutil.date_from_iso(raw)
    assert timeutil.now(UTC).utcoffset() == timedelta(0)
