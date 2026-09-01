"""Tests for ``unstd.time.dateutil`` — the typed-instant seam (``whenever``).

No test file existed for this module before. Covers ISO/instant parsing,
the naive/aware span helpers, and — the reason this file was added —
``disambiguate=`` on ``zoned_wall``/``parse_zoned`` across a real DST fold
(fall-back) and gap (spring-forward) in Europe/London.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from whenever import Instant, RepeatedTime, SkippedTime, ZonedDateTime

from unstd.time import dateutil


# Europe/London: fall-back fold on 2023-10-29 (01:00-02:00 local occurs twice),
# spring-forward gap on 2023-03-26 (01:00-02:00 local does not exist).
FOLD_ZONE = "Europe/London"
FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN = 2023, 10, 29, 1, 15
GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN = 2023, 3, 26, 1, 30


# ── parse_iso / parse_instant ───────────────────────────────────────────────


def test_parse_iso_aware() -> None:
    dt = dateutil.parse_iso("2026-01-01T00:00:00+00:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_invalid_returns_none() -> None:
    assert dateutil.parse_iso("not a date") is None


def test_parse_instant_aware() -> None:
    inst = dateutil.parse_instant("2026-01-01T00:00:00+00:00")
    assert isinstance(inst, Instant)


def test_parse_instant_naive_without_assume_utc_is_none() -> None:
    assert dateutil.parse_instant("2026-01-01T00:00:00") is None


def test_parse_instant_naive_with_assume_utc() -> None:
    inst = dateutil.parse_instant("2026-01-01T00:00:00", assume_utc=True)
    assert isinstance(inst, Instant)


def test_parse_instant_unparseable_is_none() -> None:
    assert dateutil.parse_instant("garbage") is None


# ── span_hours / span_minutes / age_days ────────────────────────────────────


def test_span_hours() -> None:
    hours = dateutil.span_hours(
        "2026-01-01T00:00:00+00:00", "2026-01-01T02:00:00+00:00"
    )
    assert hours == pytest.approx(2.0)


def test_span_hours_reads_floating_as_utc() -> None:
    hours = dateutil.span_hours("2026-01-01T00:00:00", "2026-01-01T02:00:00")
    assert hours == pytest.approx(2.0)


def test_span_hours_none_when_unparseable() -> None:
    assert dateutil.span_hours("garbage", "2026-01-01T00:00:00+00:00") is None


def test_span_minutes_strict_rejects_floating() -> None:
    assert dateutil.span_minutes("2026-01-01T00:00:00", "2026-01-01T02:00:00") is None


def test_span_minutes_aware() -> None:
    minutes = dateutil.span_minutes(
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00"
    )
    assert minutes == pytest.approx(30.0)


def test_age_days_future_is_negative() -> None:
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    assert dateutil.age_days(future) < 0


def test_age_days_past_is_positive() -> None:
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    assert dateutil.age_days(past) == pytest.approx(10.0, abs=0.01)


# ── parse_utc / minutes_until / minutes_since ───────────────────────────────


def test_parse_utc_naive_assumed_utc() -> None:
    dt = dateutil.parse_utc("2026-01-01T00:00:00")
    assert dt is not None
    assert dt.tzinfo is UTC


def test_minutes_until_future() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    minutes = dateutil.minutes_until("2026-01-01T01:00:00+00:00", now)
    assert minutes == pytest.approx(60.0)


def test_minutes_until_naive_raw_is_none() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert dateutil.minutes_until("2026-01-01T01:00:00", now) is None


def test_minutes_since_past() -> None:
    now = datetime(2026, 1, 1, 1, tzinfo=UTC)
    minutes = dateutil.minutes_since("2026-01-01T00:00:00+00:00", now)
    assert minutes == pytest.approx(60.0)


# ── zoned_now ────────────────────────────────────────────────────────────────


def test_zoned_now_is_zoned_datetime_in_requested_zone() -> None:
    z = dateutil.zoned_now("America/New_York")
    assert isinstance(z, ZonedDateTime)
    assert z.tz == "America/New_York"


# ── zoned_wall: baseline + disambiguate= ────────────────────────────────────


def test_zoned_wall_unambiguous() -> None:
    z = dateutil.zoned_wall(2026, 6, 15, 12, 0, "Europe/London")
    assert z is not None
    assert z.hour == 12


def test_zoned_wall_invalid_fields_is_none() -> None:
    assert dateutil.zoned_wall(2026, 13, 1, 0, 0, "Europe/London") is None


def test_zoned_wall_invalid_zone_is_none() -> None:
    assert dateutil.zoned_wall(2026, 1, 1, 0, 0, "Not/AZone") is None


def test_zoned_wall_default_disambiguate_is_compatible_on_fold() -> None:
    z = dateutil.zoned_wall(FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE)
    expected = ZonedDateTime(
        FOLD_Y,
        FOLD_M,
        FOLD_D,
        FOLD_H,
        FOLD_MIN,
        tz=FOLD_ZONE,
        disambiguate="compatible",
    )
    assert z == expected


def test_zoned_wall_disambiguate_earlier_vs_later_on_fold() -> None:
    earlier = dateutil.zoned_wall(
        FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="earlier"
    )
    later = dateutil.zoned_wall(
        FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="later"
    )
    assert earlier is not None
    assert later is not None
    assert earlier.to_instant() != later.to_instant()
    assert earlier.to_instant() < later.to_instant()


def test_zoned_wall_disambiguate_raise_propagates_on_fold() -> None:
    with pytest.raises(RepeatedTime):
        dateutil.zoned_wall(
            FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="raise"
        )


def test_zoned_wall_disambiguate_raise_propagates_on_gap() -> None:
    with pytest.raises(SkippedTime):
        dateutil.zoned_wall(
            GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN, FOLD_ZONE, disambiguate="raise"
        )


def test_zoned_wall_disambiguate_raise_does_not_collapse_to_none_when_fields_are_valid() -> (
    None
):
    """A ``RepeatedTime``/``SkippedTime`` is a ``ValueError`` subclass — the
    fields/zone themselves are perfectly valid, so it must not be swallowed by
    the same ``except ValueError: return None`` that handles genuinely bad
    input (e.g. ``month=13``).
    """
    with pytest.raises(ValueError, match="skipped"):
        dateutil.zoned_wall(
            GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN, FOLD_ZONE, disambiguate="raise"
        )


# ── parse_zoned: aware passthrough + naive disambiguate= ───────────────────


def test_parse_zoned_aware_converts_into_zone() -> None:
    z = dateutil.parse_zoned("2026-01-01T00:00:00+00:00", "America/New_York")
    assert z is not None
    assert z.tz == "America/New_York"
    assert z.to_instant() == Instant.from_utc(2026, 1, 1)


def test_parse_zoned_naive_reads_as_local_wall_time() -> None:
    z = dateutil.parse_zoned("2026-06-15T12:00:00", "Europe/London")
    assert z is not None
    assert z.hour == 12


def test_parse_zoned_unparseable_is_none() -> None:
    assert dateutil.parse_zoned("garbage", "Europe/London") is None


def test_parse_zoned_default_disambiguate_matches_zoned_wall_on_fold() -> None:
    raw = f"{FOLD_Y:04d}-{FOLD_M:02d}-{FOLD_D:02d}T{FOLD_H:02d}:{FOLD_MIN:02d}:00"
    via_parse = dateutil.parse_zoned(raw, FOLD_ZONE)
    via_wall = dateutil.zoned_wall(FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE)
    assert via_parse == via_wall


def test_parse_zoned_disambiguate_raise_propagates_on_fold() -> None:
    raw = f"{FOLD_Y:04d}-{FOLD_M:02d}-{FOLD_D:02d}T{FOLD_H:02d}:{FOLD_MIN:02d}:00"
    with pytest.raises(RepeatedTime):
        dateutil.parse_zoned(raw, FOLD_ZONE, disambiguate="raise")


def test_parse_zoned_disambiguate_moot_for_aware_input() -> None:
    """An aware ``raw`` carries its own absolute instant — no fold/gap to
    resolve — so ``disambiguate`` must not affect (or be rejected for) it.
    """
    z = dateutil.parse_zoned(
        "2026-01-01T00:00:00+00:00", "Europe/London", disambiguate="raise"
    )
    assert z is not None


# ── zoned_to_utc / hours_between / iso_range / lookback / ranges ───────────


def test_zoned_to_utc() -> None:
    z = dateutil.zoned_wall(2026, 6, 15, 12, 0, "Europe/London")
    assert z is not None
    utc = dateutil.zoned_to_utc(z)
    assert utc.tzinfo is UTC
    assert utc.hour == 11  # BST is UTC+1 in June


def test_hours_between() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    assert dateutil.hours_between(start, end) == 2.5


def test_lookback_default() -> None:
    start, end = dateutil.lookback()
    assert (end - start) == timedelta(hours=24)


def test_lookback_custom_hours() -> None:
    start, end = dateutil.lookback(hours=1)
    assert (end - start) == timedelta(hours=1)


def test_days_range() -> None:
    start, end = dateutil.days_range(7)
    assert (end - start) == timedelta(days=7)
    assert isinstance(start, date)


def test_day_offset() -> None:
    base = date(2026, 1, 1)
    assert dateutil.day_offset(base, 5) == date(2026, 1, 6)
    assert dateutil.day_offset(base, -1) == date(2025, 12, 31)


def test_parse_date() -> None:
    assert dateutil.parse_date("2026-01-01") == date(2026, 1, 1)


def test_from_epoch_s() -> None:
    dt = dateutil.from_epoch_s(0)
    assert dt == datetime(1970, 1, 1, tzinfo=UTC)


def test_from_epoch_ms() -> None:
    dt = dateutil.from_epoch_ms(1000)
    assert dt == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


def test_iso_range() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    s, e = dateutil.iso_range(start, end)
    assert dateutil.parse_iso(s) == start
    assert dateutil.parse_iso(e) == end
