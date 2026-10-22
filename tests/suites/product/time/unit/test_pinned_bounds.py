"""Pinned clocks, calendar bounds, fraction precision and the protobuf bridge.

Every clock read is pinned by injection, so expected strings are calendar facts
written by hand rather than a second read of a moving clock. Timestamp range and
precision come from ``google/protobuf/timestamp.proto``: seconds in
[0001-01-01T00:00:00Z, 9999-12-31T23:59:59Z] and nanoseconds in [0, 999999999].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import os
import sys
import time as _time
from typing import TYPE_CHECKING, ClassVar
from zoneinfo import ZoneInfo

import pytest

from tests.support.environment.backends import require_backend
from unstd.time import timeutil


if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import tzinfo


# Thirty minutes after New York springs forward; St. John's changed at 05:30Z.
INSTANT = datetime(2026, 3, 8, 7, 30, 0, 987_654, tzinfo=UTC)
KATHMANDU = ZoneInfo("Asia/Kathmandu")
NEW_YORK = ZoneInfo("America/New_York")


class _Pinned(datetime):
    """``datetime`` whose ``now`` always names :data:`INSTANT`."""

    instant: ClassVar[datetime] = INSTANT

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        if tz is None:
            return datetime.fromtimestamp(cls.instant.timestamp())  # noqa: DTZ006 - host-local by contract
        return cls.instant.astimezone(tz)


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timeutil, "datetime", _Pinned)


@pytest.fixture
def los_angeles_host() -> Iterator[None]:
    if not hasattr(_time, "tzset"):
        pytest.skip("host zone pinning needs POSIX time.tzset")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    _time.tzset()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        _time.tzset()


# ── pinned clocks ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        ("America/New_York", "Sunday, March 8, 2026 at 3:30 AM EDT"),
        ("America/St_Johns", "Sunday, March 8, 2026 at 5:00 AM NDT"),
        ("Europe/London", "Sunday, March 8, 2026 at 7:30 AM GMT"),
        ("Australia/Sydney", "Sunday, March 8, 2026 at 6:30 PM AEDT"),
        ("Australia/Lord_Howe", "Sunday, March 8, 2026 at 6:30 PM +11"),
        ("Asia/Kathmandu", "Sunday, March 8, 2026 at 1:15 PM +0545"),
        ("Pacific/Chatham", "Sunday, March 8, 2026 at 9:15 PM +1345"),
        ("UTC", "Sunday, March 8, 2026 at 7:30 AM UTC"),
    ],
)
@pytest.mark.usefixtures("pinned")
def test_human_renders_the_pinned_instant_in_each_zone(
    zone: str, expected: str
) -> None:
    assert timeutil.human(zone) == expected


@pytest.mark.usefixtures("pinned", "los_angeles_host")
def test_human_and_today_follow_the_host_zone() -> None:
    assert timeutil.human() == "Saturday, March 7, 2026 at 11:30 PM PST"
    assert timeutil.today() == date(2026, 3, 7)
    assert timeutil.today(utc=True) == date(2026, 3, 8)


@pytest.mark.usefixtures("pinned")
def test_iso_and_now_tz_read_the_pinned_instant() -> None:
    assert timeutil.iso() == "2026-03-08T07:30:00Z"
    assert timeutil.iso(ms=True) == "2026-03-08T07:30:00.987Z"
    local = timeutil.now_tz(KATHMANDU)
    assert local == INSTANT
    assert (local.hour, local.minute) == (13, 15)
    assert local.utcoffset() == timedelta(hours=5, minutes=45)


@pytest.mark.usefixtures("pinned")
def test_span_to_now_measures_from_the_pinned_instant() -> None:
    assert timeutil.span("2026-03-08T01:00:00-05:00", unit="seconds") == 5400.987654
    assert timeutil.span("2026-03-08T13:15:00.987654+05:45", unit="days") == 0.0
    assert timeutil.span("2026-03-08T07:30:00", unit="hours") is None
    assert (
        timeutil.span("2026-03-08T07:30:00.987654", unit="hours", assume_utc=True)
        == 0.0
    )


@pytest.mark.parametrize(
    ("ns", "seconds", "millis"),
    [
        (0, 0, 0),
        (999_999, 0, 0),
        (1_768_487_400_123_456_789, 1_768_487_400, 1_768_487_400_123),
        (1_768_487_400_999_999_999, 1_768_487_400, 1_768_487_400_999),
        (2**63 - 1, 9_223_372_036, 9_223_372_036_854),
    ],
)
def test_epoch_reads_truncate_a_pinned_nanosecond_clock(
    ns: int, seconds: int, millis: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(timeutil._time, "time_ns", lambda: ns)
    assert (timeutil.epoch_s(), timeutil.epoch_ms()) == (seconds, millis)
    assert type(timeutil.epoch_s()) is int
    assert type(timeutil.epoch_ms()) is int


def test_elapsed_ms_reads_the_perf_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timeutil._time, "perf_counter", lambda: 12.5)
    assert timeutil.elapsed_ms(10.0) == 2500.0
    assert timeutil.elapsed_ms(13.0) == -500.0


def test_clock_names_are_the_stdlib_functions() -> None:
    assert timeutil.wall is _time.time
    assert timeutil.mono is _time.monotonic
    assert timeutil.mono_ns is _time.monotonic_ns
    assert timeutil.perf is _time.perf_counter


# ── calendar bounds and fraction precision ───────────────────────────────────


@pytest.mark.parametrize(
    ("value", "seconds", "millis"),
    [
        (
            datetime(1, 1, 1, tzinfo=UTC),
            "0001-01-01T00:00:00Z",
            "0001-01-01T00:00:00.000Z",
        ),
        (
            datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC),
            "9999-12-31T23:59:59Z",
            "9999-12-31T23:59:59.999Z",
        ),
        (
            datetime(
                1, 1, 1, 5, 45, 0, 999, tzinfo=timezone(timedelta(hours=5, minutes=45))
            ),
            "0001-01-01T00:00:00Z",
            "0001-01-01T00:00:00.000Z",
        ),
        (
            datetime(
                9999,
                12,
                31,
                18,
                29,
                59,
                1_000,
                tzinfo=timezone(timedelta(hours=-5, minutes=-30)),
            ),
            "9999-12-31T23:59:59Z",
            "9999-12-31T23:59:59.001Z",
        ),
    ],
)
def test_iso_at_the_year_limits_truncates_fractions(
    value: datetime, seconds: str, millis: str
) -> None:
    assert timeutil.iso(value) == seconds
    assert timeutil.iso(value, ms=True) == millis


def test_parsers_reach_both_year_limits() -> None:
    assert timeutil.parse_utc("0001-01-01T00:00:00Z") == datetime(1, 1, 1, tzinfo=UTC)
    assert timeutil.parse_utc("9999-12-31T23:59:59.999999Z") == datetime(
        9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC
    )
    assert timeutil.parse_date("0001-01-01") == date.min
    assert timeutil.parse_date("9999-12-31") == date.max
    assert timeutil.parse_date("10000-01-01") is None


def test_parsing_truncates_nanoseconds_to_microseconds() -> None:
    """Python documents ``fromisoformat`` truncating digits beyond six."""
    assert timeutil.parse_utc("2026-01-15T14:30:00.123456789Z") == datetime(
        2026, 1, 15, 14, 30, 0, 123_456, tzinfo=UTC
    )
    assert timeutil.parse_utc("2026-01-15T14:30:00.999999999Z") == datetime(
        2026, 1, 15, 14, 30, 0, 999_999, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("raw", "offset"),
    [
        ("2026-03-08T13:15:00+05:45", timedelta(hours=5, minutes=45)),
        ("2026-03-08T21:15:00+13:45", timedelta(hours=13, minutes=45)),
        ("1883-11-18T12:00:00-04:56:02", -timedelta(hours=4, minutes=56, seconds=2)),
    ],
)
def test_parse_keeps_odd_offsets_and_their_instant(raw: str, offset: timedelta) -> None:
    parsed = timeutil.parse_iso(raw)
    assert parsed is not None
    assert parsed.utcoffset() == offset
    utc = timeutil.parse_utc(raw)
    assert utc is not None
    assert utc.tzinfo is UTC
    assert utc.replace(tzinfo=None) == parsed.replace(tzinfo=None) - offset


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)),
        (-62_135_596_800, datetime(1, 1, 1, tzinfo=UTC)),
        (253_402_300_799, datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)),
    ],
)
def test_from_epoch_s_negative_and_limit_seconds(
    seconds: int, expected: datetime
) -> None:
    try:
        reference = datetime.fromtimestamp(seconds, UTC)
    except (OSError, OverflowError, ValueError) as error:
        expected_type, expected_args = type(error), error.args
        with pytest.raises(expected_type) as actual:
            timeutil.from_epoch_s(seconds)
        assert type(actual.value) is expected_type
        assert actual.value.args == expected_args
        return
    assert reference == expected
    assert timeutil.from_epoch_s(seconds) == expected


@pytest.mark.parametrize("seconds", [253_402_300_800, -62_135_596_801])
def test_from_epoch_s_beyond_the_calendar_raises(seconds: int) -> None:
    with pytest.raises((OSError, OverflowError, ValueError)) as reference:
        datetime.fromtimestamp(seconds, UTC)
    with pytest.raises(type(reference.value)) as actual:
        timeutil.from_epoch_s(seconds)
    assert type(actual.value) is type(reference.value)
    assert actual.value.args == reference.value.args


@pytest.mark.parametrize(
    ("seconds", "offset"),
    [
        (0, timedelta(hours=5, minutes=30)),  # Nepal was +05:30 until 1986
        (-2_208_988_800, timedelta(hours=5, minutes=41, seconds=16)),  # 1900 LMT
        (504_901_800, timedelta(hours=5, minutes=45)),  # 1986-01-01T00:15+05:45
    ],
)
def test_from_epoch_uses_the_historical_zone_offset(
    seconds: int, offset: timedelta
) -> None:
    try:
        reference = datetime.fromtimestamp(seconds, KATHMANDU)
    except (OSError, OverflowError, ValueError) as error:
        expected_type, expected_args = type(error), error.args
        with pytest.raises(expected_type) as actual_seconds:
            timeutil.from_epoch_s(seconds, KATHMANDU)
        assert type(actual_seconds.value) is expected_type
        assert actual_seconds.value.args == expected_args
        with pytest.raises(expected_type) as actual_millis:
            timeutil.from_epoch_ms(seconds * 1000, KATHMANDU)
        assert type(actual_millis.value) is expected_type
        assert actual_millis.value.args == expected_args
        return
    local = timeutil.from_epoch_s(seconds, KATHMANDU)
    assert local == reference
    assert local.utcoffset() == offset
    assert local == datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    assert timeutil.from_epoch_ms(seconds * 1000, KATHMANDU).utcoffset() == offset


# ── protobuf bridge: absent ──────────────────────────────────────────────────


@pytest.fixture
def fresh_bridge() -> Iterator[None]:
    timeutil._timestamp.cache_clear()
    yield
    timeutil._timestamp.cache_clear()


@pytest.mark.usefixtures("fresh_bridge")
@pytest.mark.parametrize(
    "call",
    [
        lambda: timeutil.proto_timestamp("2026-01-15T14:30:00Z"),
        lambda: timeutil.proto_timestamp(""),
        lambda: timeutil.proto_timestamp(None),
        lambda: timeutil.proto_timestamp_ms(0),
    ],
    ids=["string", "empty", "none", "millis"],
)
def test_absent_protobuf_names_the_extra_and_recovers(
    call: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "google.protobuf.timestamp_pb2", None)
    with pytest.raises(ImportError, match=r"pip install 'unstd\[proto\]'") as caught:
        call()  # type: ignore[operator]
    assert type(caught.value.__cause__) is ModuleNotFoundError
    with pytest.raises(ImportError, match=r"unstd\[proto\]"):
        call()  # type: ignore[operator]  # a failed import is not cached
    monkeypatch.undo()
    require_backend("google.protobuf")
    assert timeutil.proto_timestamp_ms(1).ToMilliseconds() == 1


# ── protobuf bridge: present ─────────────────────────────────────────────────


def _timestamp_type() -> type:
    return require_backend("google.protobuf.timestamp_pb2").Timestamp


@pytest.mark.parametrize(
    ("value", "seconds", "nanos"),
    [
        (datetime(1, 1, 1, tzinfo=UTC), -62_135_596_800, 0),
        ("0001-01-01T00:00:00Z", -62_135_596_800, 0),
        (
            datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC),
            253_402_300_799,
            999_999_000,
        ),
        ("9999-12-31T23:59:59.999999999Z", 253_402_300_799, 999_999_000),
        ("2026-01-15T14:30:00.123456789Z", 1_768_487_400, 123_456_000),
        ("1969-12-31T23:59:59.5Z", -1, 500_000_000),
        (datetime(2026, 1, 15, 14, 30), 1_768_487_400, 0),  # noqa: DTZ001 - naive is UTC by contract
        ("2026-01-15T20:15:00+05:45", 1_768_487_400, 0),
        (datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK), 1_793_511_000, 0),
        (datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=NEW_YORK), 1_793_514_600, 0),
    ],
    ids=[
        "min-datetime",
        "min-string",
        "max-datetime",
        "max-nanos-truncated",
        "nanos-truncated",
        "pre-epoch-fraction",
        "naive",
        "kathmandu",
        "fold-first",
        "fold-second",
    ],
)
def test_proto_timestamp_range_and_precision(
    value: str | datetime, seconds: int, nanos: int
) -> None:
    timestamp = _timestamp_type()
    ts = timeutil.proto_timestamp(value)
    assert type(ts) is timestamp
    assert (ts.seconds, ts.nanos) == (seconds, nanos)


@pytest.mark.parametrize(
    ("ms", "seconds", "nanos"),
    [
        (-62_135_596_800_000, -62_135_596_800, 0),
        (253_402_300_799_999, 253_402_300_799, 999_000_000),
        (-1, -1, 999_000_000),
    ],
)
def test_proto_timestamp_ms_reaches_the_declared_limits(
    ms: int, seconds: int, nanos: int
) -> None:
    timestamp = _timestamp_type()
    ts = timeutil.proto_timestamp_ms(ms)
    assert type(ts) is timestamp
    assert (ts.seconds, ts.nanos) == (seconds, nanos)


@pytest.mark.parametrize("ms", [253_402_300_800_000, -62_135_596_800_001])
def test_proto_timestamp_ms_rejects_outside_the_declared_range(ms: int) -> None:
    _timestamp_type()
    with pytest.raises(ValueError, match="Timestamp is not valid"):
        timeutil.proto_timestamp_ms(ms)
