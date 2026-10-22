"""Generated absolute-instant laws for ``unstd.time.timeutil``.

Oracle: integer microseconds since 1970-01-01T00:00Z, computed from each value's
wall fields minus its own ``utcoffset()``, and UTC calendar fields rebuilt from
that integer with ``date.fromordinal``. Neither path calls ``astimezone``, which
the module itself uses. Platform timestamp rejection is checked independently
with ``datetime.fromtimestamp`` before asserting exact supported calendar values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import re
from typing import TYPE_CHECKING
from unittest.mock import patch
from zoneinfo import ZoneInfo

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.time import timeutil


if TYPE_CHECKING:
    from collections.abc import Callable


_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_US = 1_000_000
_DAY_US = 86_400 * _US
_MS_MIN, _MS_MAX = -62_135_596_800_000, 253_402_300_799_999
ZONES = [
    "UTC",
    "America/New_York",
    "America/St_Johns",
    "Europe/London",
    "Australia/Sydney",
    "Australia/Lord_Howe",
    "Asia/Kathmandu",
    "Pacific/Chatham",
    "Pacific/Kiritimati",
    "Pacific/Pago_Pago",
]

_fixed = st.integers(-(24 * 3600 - 1), 24 * 3600 - 1).map(
    lambda s: timezone(timedelta(seconds=s))
)
_zones = st.sampled_from(ZONES).map(ZoneInfo)
aware = st.datetimes(
    min_value=datetime(2, 1, 1),  # noqa: DTZ001 - bounds for an aware strategy
    max_value=datetime(9998, 12, 31),  # noqa: DTZ001
    timezones=st.one_of(_fixed, _zones),
)


def _epoch_us(dt: datetime) -> int:
    offset = dt.utcoffset()
    assert offset is not None
    wall = dt.replace(tzinfo=None)
    days = wall.toordinal() - _EPOCH_ORDINAL
    seconds = wall.hour * 3600 + wall.minute * 60 + wall.second
    return (
        days * _DAY_US
        + seconds * _US
        + wall.microsecond
        - offset // timedelta(microseconds=1)
    )


def _utc_stamp(us: int) -> tuple[str, int]:
    days, rest = divmod(us, _DAY_US)
    day = date.fromordinal(_EPOCH_ORDINAL + days)
    seconds, micro = divmod(rest, _US)
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    return (
        f"{day.year:04d}-{day.month:02d}-{day.day:02d}T{hh:02d}:{mm:02d}:{ss:02d}",
        micro,
    )


def _platform_supports_timestamp(
    seconds: int, tz: ZoneInfo | timezone, operation: Callable[[], datetime]
) -> bool:
    try:
        datetime.fromtimestamp(seconds, tz)
    except (OSError, OverflowError, ValueError) as error:
        with pytest.raises(type(error), match=f"^{re.escape(str(error))}$"):
            operation()
        return False
    return True


@given(aware)
@example(datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ZoneInfo("America/New_York")))
@example(datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York")))
@example(
    datetime(2, 1, 1, 0, 0, 0, 999, tzinfo=timezone(timedelta(hours=23, minutes=59)))
)
def test_iso_names_the_utc_instant_and_truncates(dt: datetime) -> None:
    stamp, micro = _utc_stamp(_epoch_us(dt))
    assert timeutil.iso(dt) == f"{stamp}Z"
    assert timeutil.iso(dt, ms=True) == f"{stamp}.{micro // 1000:03d}Z"


@given(aware)
def test_parsers_round_trip_the_instant_and_offset(dt: datetime) -> None:
    text = dt.isoformat()
    parsed = timeutil.parse_iso(text)
    assert parsed is not None
    assert parsed.utcoffset() == dt.utcoffset()
    assert _epoch_us(parsed) == _epoch_us(dt)
    utc = timeutil.parse_utc(text)
    assert utc is not None
    assert utc.tzinfo is UTC
    assert _epoch_us(utc) == _epoch_us(dt)
    back = timeutil.parse_utc(timeutil.iso(dt, ms=True))
    assert back is not None
    assert 0 <= _epoch_us(dt) - _epoch_us(back) < 1000


@given(aware, aware)
@example(
    datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York")),
    datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ZoneInfo("America/New_York")),
)
def test_span_is_the_signed_instant_difference(a: datetime, b: datetime) -> None:
    delta_us = _epoch_us(b) - _epoch_us(a)
    seconds = delta_us / _US
    assert timeutil.span(a, b, unit="seconds") == seconds
    assert timeutil.span(b, a, unit="seconds") == -seconds
    assert timeutil.span(a.isoformat(), b.isoformat(), unit="seconds") == seconds
    for unit, size in (("minutes", 60), ("hours", 3600), ("days", 86_400)):
        assert timeutil.span(a, b, unit=unit) == pytest.approx(
            delta_us / (size * _US), rel=1e-15, abs=1e-15
        )  # type: ignore[arg-type]
    floating = timeutil.span(
        a.replace(tzinfo=None), b.replace(tzinfo=None), unit="seconds", assume_utc=True
    )
    wall_us = _epoch_us(b.replace(tzinfo=UTC)) - _epoch_us(a.replace(tzinfo=UTC))
    assert floating == wall_us / _US
    assert timeutil.span(a.replace(tzinfo=None), b, unit="seconds") is None


@given(st.integers(_MS_MIN, _MS_MAX))
@example(_MS_MIN)
@example(_MS_MAX)
@example(-1)
def test_from_epoch_ms_is_exact_across_the_calendar(ms: int) -> None:
    if not _platform_supports_timestamp(
        ms // 1000, UTC, lambda: timeutil.from_epoch_ms(ms)
    ):
        return
    dt = timeutil.from_epoch_ms(ms)
    assert dt.tzinfo is UTC
    assert _epoch_us(dt) == ms * 1000


@given(st.integers(_MS_MIN + _DAY_US // 1000, _MS_MAX - _DAY_US // 1000), _zones)
def test_from_epoch_ms_and_s_convert_into_any_zone(ms: int, tz: ZoneInfo) -> None:
    if _platform_supports_timestamp(
        ms // 1000, UTC, lambda: timeutil.from_epoch_ms(ms, tz)
    ):
        dt = timeutil.from_epoch_ms(ms, tz)
        assert dt.tzinfo is tz
        assert _epoch_us(dt) == ms * 1000
    if _platform_supports_timestamp(
        ms // 1000, tz, lambda: timeutil.from_epoch_s(ms // 1000, tz)
    ):
        whole = timeutil.from_epoch_s(ms // 1000, tz)
        assert whole.tzinfo is tz
        assert _epoch_us(whole) == ms // 1000 * _US


@given(st.integers(0, 2**63 - 1))
@example(999_999)
@example(1_000_000)
def test_epoch_reads_floor_a_pinned_clock(ns: int) -> None:
    with patch.object(timeutil._time, "time_ns", lambda: ns):
        ms, s = timeutil.epoch_ms(), timeutil.epoch_s()
    assert ms * 1_000_000 <= ns < (ms + 1) * 1_000_000
    assert s * 1_000_000_000 <= ns < (s + 1) * 1_000_000_000
