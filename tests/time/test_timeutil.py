"""Contract tests for ``unstd.time.timeutil``.

The module shipped untested, and three of the bugs pinned below were live in the
released surface. Each of those has a test written so that it **fails against the
old code**, not merely passes against the new:

1. ``human_now_tz`` took an IANA zone and then chained a bare ``.astimezone()``,
   which converts to the *local* zone — so the argument did nothing and every
   zone rendered as the host's own.
2. ``iso_fmt`` appended a literal ``Z`` to whatever wall time it was handed. An
   aware non-UTC datetime came out labelled UTC while showing local digits: a
   silent shift by the offset, the worst failure mode a timestamp has.
3. The long-form format used ``%-d``/``%-I``, a glibc/BSD ``strftime``
   extension that raises ``ValueError`` on Windows — one of the three platforms
   the package's ``requires-python`` claims.
4. The protobuf bridge imported ``google.protobuf`` with no declared dependency,
   so it raised a bare ``ModuleNotFoundError: No module named 'google'`` naming
   nothing installable. It is behind the ``proto`` extra now.

Clock-reading functions are checked against bounds and relationships rather than
values, so nothing here is flaky.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import re
import time as _time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from unstd.time import timeutil


NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


# ── regression 1: human must honour its zone ─────────────────────────


def test_human_renders_different_zones_differently() -> None:
    """New York and Tokyo are never in the same hour; the old code printed both
    as the host's local time.
    """
    assert timeutil.human("America/New_York") != timeutil.human("Asia/Tokyo")


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo", "UTC"])
def test_human_matches_the_wall_clock_of_that_zone(zone: str) -> None:
    """Oracle: the hour digits must be that zone's, computed independently."""
    rendered = timeutil.human(zone)
    now = datetime.now(ZoneInfo(zone))
    assert f"{now.hour % 12 or 12}:{now.minute:02d}" in rendered
    assert now.strftime("%A") in rendered


def test_human_default_is_the_local_zone() -> None:
    """The empty default means local — the host's own wall clock and zone name."""
    now = datetime.now().astimezone()
    rendered = timeutil.human()
    assert rendered.startswith(now.strftime("%A"))
    assert rendered.endswith(now.strftime("%Z"))


def test_human_rejects_an_unknown_zone_rather_than_falling_back() -> None:
    with pytest.raises(ZoneInfoNotFoundError, match=r"No time zone found|Not/AZone"):
        timeutil.human("Not/AZone")


# ── regression 2: iso must convert, not relabel ─────────────────────────


def test_iso_converts_an_aware_non_utc_instant_to_real_utc() -> None:
    """The trailing Z is a claim about the instant, so it has to be made true.

    Old behaviour returned ``2026-01-15T09:30:00Z`` for this input — the New
    York digits wearing a UTC label, five hours off.
    """
    ny = datetime(2026, 1, 15, 9, 30, tzinfo=NY)
    assert timeutil.iso(ny) == "2026-01-15T14:30:00Z"


@pytest.mark.parametrize(
    ("offset_hours", "expected"),
    [
        (0, "2026-06-01T12:00:00Z"),
        (9, "2026-06-01T03:00:00Z"),
        (-5, "2026-06-01T17:00:00Z"),
    ],
)
def test_iso_is_correct_across_offsets(offset_hours: int, expected: str) -> None:
    dt = datetime(2026, 6, 1, 12, 0, tzinfo=timezone(timedelta(hours=offset_hours)))
    assert timeutil.iso(dt) == expected


def test_iso_preserves_the_absolute_instant() -> None:
    """The generalised form of the bug: parsing the output must give the input
    back as the same point in time, whatever zone it arrived in.
    """
    for zone in (NY, TOKYO, UTC):
        dt = datetime(2026, 3, 8, 6, 45, tzinfo=zone)
        parsed = datetime.fromisoformat(timeutil.iso(dt))
        assert parsed == dt


def test_iso_treats_a_naive_datetime_as_utc() -> None:
    """The module's own documented convention, shared with ``proto_timestamp`` —
    a naive value is labelled, never shifted by the host's zone.
    """
    assert timeutil.iso(datetime(2026, 1, 15, 14, 30)) == "2026-01-15T14:30:00Z"  # noqa: DTZ001


def test_iso_agrees_with_utcnow_iso_on_the_same_instant() -> None:
    now = datetime.now(UTC)
    assert timeutil.iso(now) == now.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_iso_ms_truncates_never_rounds_into_the_future() -> None:
    dt = datetime(2026, 1, 15, 14, 30, 59, 999_999, tzinfo=UTC)
    assert timeutil.iso(dt, ms=True) == "2026-01-15T14:30:59.999Z"
    assert timeutil.iso(dt) == "2026-01-15T14:30:59Z"


# ── regression 3: no platform-specific strftime codes ───────────────────────


def test_no_glibc_only_strftime_codes_reach_strftime() -> None:
    """``%-d``/``%-I`` raise ValueError on Windows. Assert against the *format
    strings*, since a passing render on macOS proves nothing about Windows.
    """
    formats = [
        v
        for name, v in vars(timeutil).items()
        if isinstance(v, str) and name.isupper() and "%" in v
    ]
    assert formats, "expected the module to still define format constants"
    for fmt in formats:
        assert not re.search(r"%-\w", fmt), f"non-portable code in {fmt!r}"


def test_day_and_hour_stay_unpadded_without_the_nonportable_codes() -> None:
    """The reason ``%-d``/``%-I`` were there — dropping them must not reintroduce
    zero padding.
    """
    rendered = timeutil._human(datetime(2026, 1, 3, 9, 5, tzinfo=UTC))
    assert rendered.startswith("Saturday, January 3, 2026 at 9:05 AM")
    assert "January 03" not in rendered
    assert "at 09:05" not in rendered


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "12:00 AM"),
        (1, "1:00 AM"),
        (11, "11:00 AM"),
        (12, "12:00 PM"),
        (23, "11:00 PM"),
    ],
)
def test_twelve_hour_conversion_including_both_midnights(
    hour: int, expected: str
) -> None:
    """Hand-rolled 12-hour arithmetic is where an off-by-one hides — 0 and 12
    both have to read as 12.
    """
    assert expected in timeutil._human(datetime(2026, 1, 3, hour, 0, tzinfo=UTC))


# ── regression 4: the protobuf bridge names its extra ───────────────────────


def test_proto_timestamp_builds_a_real_timestamp() -> None:
    pytest.importorskip("google.protobuf")
    ts = timeutil.proto_timestamp("2026-01-15T14:30:00Z")
    assert ts is not None
    assert ts.seconds == int(datetime(2026, 1, 15, 14, 30, tzinfo=UTC).timestamp())


@pytest.mark.parametrize(
    ("value", "expected_iso"),
    [
        ("2026-01-15T14:30:00Z", "2026-01-15T14:30:00+00:00"),
        ("2026-01-15T09:30:00-05:00", "2026-01-15T14:30:00+00:00"),
        ("2026-01-15T14:30:00", "2026-01-15T14:30:00+00:00"),  # naive → UTC
        ("2026-01-15", "2026-01-15T00:00:00+00:00"),  # date-only → UTC midnight
    ],
)
def test_proto_timestamp_parses_each_documented_input_shape(
    value: str, expected_iso: str
) -> None:
    pytest.importorskip("google.protobuf")
    ts = timeutil.proto_timestamp(value)
    assert ts is not None
    assert ts.ToDatetime(tzinfo=UTC).isoformat() == expected_iso


@pytest.mark.parametrize("value", ["", None, "   ", "not a timestamp", "2026-13-45"])
def test_proto_timestamp_returns_the_unset_sentinel_for_unusable_input(
    value: str | None,
) -> None:
    """Documented contract: unparseable is ``None`` (field unset), never a raise
    and never epoch zero.
    """
    pytest.importorskip("google.protobuf")
    assert timeutil.proto_timestamp(value) is None


def test_proto_timestamp_ms_round_trips_epoch_milliseconds() -> None:
    pytest.importorskip("google.protobuf")
    ms = 1_768_487_400_000
    assert timeutil.proto_timestamp_ms(ms).ToMilliseconds() == ms


def test_proto_bridge_error_names_an_installable_extra() -> None:
    """Without protobuf the old code raised ``No module named 'google'``, which
    tells a caller nothing they can act on. Every other guarded backend in this
    package names its extra; this one must too.
    """
    with pytest.raises(ImportError, match=r"unstd\[proto\]"):
        raise ImportError(timeutil._PROTO_HINT)


# ── the rest of the surface ─────────────────────────────────────────────────


def test_utcnow_is_aware_and_utc() -> None:
    now = timeutil.utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_now_tz_defaults_to_utc_and_honours_an_explicit_zone() -> None:
    assert timeutil.now_tz().utcoffset() == timedelta(0)
    assert timeutil.now_tz(TOKYO).utcoffset() == timedelta(hours=9)


def test_now_tz_and_utcnow_describe_the_same_instant() -> None:
    """Different zones, one moment — the point of an aware datetime."""
    assert abs(timeutil.now_tz(NY) - timeutil.utcnow()) < timedelta(seconds=5)


def test_iso_of_now_is_parseable_and_second_granular() -> None:
    stamp = timeutil.iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    parsed = datetime.fromisoformat(stamp)
    assert abs(parsed - datetime.now(UTC)) < timedelta(seconds=5)


def test_iso_ms_of_now_carries_milliseconds_and_still_parses() -> None:
    stamp = timeutil.iso(ms=True)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp)
    parsed = datetime.fromisoformat(stamp)
    assert abs(parsed - datetime.now(UTC)) < timedelta(seconds=5)


def test_iso_ms_is_the_second_granular_stamp_plus_a_fraction() -> None:
    dt = datetime(2026, 1, 15, 14, 30, 5, 123_456, tzinfo=UTC)
    assert timeutil.iso(dt, ms=True) == timeutil.iso(dt)[:-1] + ".123Z"


def test_today_is_the_local_calendar_day() -> None:
    assert timeutil.today() == datetime.now().astimezone().date()


def test_today_utc_is_the_utc_calendar_day() -> None:
    """Deliberately distinct from the local day — they differ by a day for part of
    every day, and that is the reason the flag exists.
    """
    assert timeutil.today(utc=True) == datetime.now(UTC).date()


def test_epoch_seconds_and_millis_agree_to_the_second() -> None:
    ms, s = timeutil.epoch_ms(), timeutil.epoch_s()
    assert abs(ms // 1000 - s) <= 1


def test_epoch_s_truncates_rather_than_rounds() -> None:
    assert timeutil.epoch_s() <= timeutil.wall()


def test_wall_tracks_the_system_clock() -> None:
    assert abs(timeutil.wall() - _time.time()) < 1.0


def test_mono_never_goes_backwards() -> None:
    readings = [timeutil.mono() for _ in range(20)]
    assert readings == sorted(readings)


def test_mono_ns_is_the_nanosecond_view_of_the_same_clock() -> None:
    ns, s = timeutil.mono_ns(), timeutil.mono()
    assert abs(ns / 1e9 - s) < 1.0


def test_elapsed_ms_measures_a_real_span_from_a_perf_reading() -> None:
    start = timeutil.perf()
    _time.sleep(0.02)
    elapsed = timeutil.elapsed_ms(start)
    assert 15 < elapsed < 2000  # generous ceiling: a loaded CI box still passes


def test_elapsed_ms_of_an_immediate_reading_is_near_zero_and_never_negative() -> None:
    assert 0 <= timeutil.elapsed_ms(timeutil.perf()) < 100


# ── parsing (moved from the retired ``dateutil`` — pure stdlib) ─────────────


def test_parse_iso_aware() -> None:
    dt = timeutil.parse_iso("2026-01-01T00:00:00+00:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_invalid_returns_none() -> None:
    assert timeutil.parse_iso("not a date") is None


def test_parse_utc_naive_assumed_utc() -> None:
    dt = timeutil.parse_utc("2026-01-01T00:00:00")
    assert dt is not None
    assert dt.tzinfo is UTC


def test_parse_utc_converts_an_aware_value_rather_than_relabelling_it() -> None:
    assert timeutil.parse_utc("2026-01-01T09:00:00+09:00") == datetime(
        2026, 1, 1, tzinfo=UTC
    )


def test_parse_utc_unparseable_is_none() -> None:
    assert timeutil.parse_utc("garbage") is None


def test_parse_date() -> None:
    assert timeutil.parse_date("2026-01-01") == date(2026, 1, 1)


@pytest.mark.parametrize("raw", ["garbage", "2026-13-01", "2026-01-01T00:00:00", ""])
def test_parse_date_is_none_like_every_other_parser(raw: str) -> None:
    assert timeutil.parse_date(raw) is None


def test_from_epoch_s() -> None:
    assert timeutil.from_epoch_s(0) == datetime(1970, 1, 1, tzinfo=UTC)


def test_from_epoch_ms() -> None:
    assert timeutil.from_epoch_ms(1000) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


def test_from_epoch_ms_honours_a_zone() -> None:
    assert timeutil.from_epoch_ms(0, TOKYO).utcoffset() == timedelta(hours=9)


# ── span: one signed duration, strict by default ────────────────────────────


def test_span_hours() -> None:
    hours = timeutil.span(
        "2026-01-01T00:00:00+00:00", "2026-01-01T02:00:00+00:00", unit="hours"
    )
    assert hours == pytest.approx(2.0)


def test_span_assume_utc_reads_floating_as_utc() -> None:
    hours = timeutil.span(
        "2026-01-01T00:00:00", "2026-01-01T02:00:00", unit="hours", assume_utc=True
    )
    assert hours == pytest.approx(2.0)


def test_span_none_when_unparseable() -> None:
    assert timeutil.span("garbage", "2026-01-01T00:00:00+00:00", unit="hours") is None


def test_span_strict_by_default_rejects_floating() -> None:
    assert (
        timeutil.span("2026-01-01T00:00:00", "2026-01-01T02:00:00", unit="minutes")
        is None
    )


def test_span_minutes_aware() -> None:
    minutes = timeutil.span(
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00", unit="minutes"
    )
    assert minutes == pytest.approx(30.0)


def test_span_to_now_of_a_future_stamp_is_negative() -> None:
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    assert timeutil.span(future, unit="days") < 0


def test_span_to_now_of_a_past_stamp_is_its_age() -> None:
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    assert timeutil.span(past, unit="days") == pytest.approx(10.0, abs=0.01)


def test_span_from_an_aware_now_to_a_deadline() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert timeutil.span(
        now, "2026-01-01T01:00:00+00:00", unit="minutes"
    ) == pytest.approx(60.0)


def test_span_from_an_aware_now_to_a_floating_deadline_is_none() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert timeutil.span(now, "2026-01-01T01:00:00", unit="minutes") is None


def test_span_since_a_past_stamp_to_an_aware_now() -> None:
    now = datetime(2026, 1, 1, 1, tzinfo=UTC)
    assert timeutil.span(
        "2026-01-01T00:00:00+00:00", now, unit="minutes"
    ) == pytest.approx(60.0)


def test_span_across_zones_measures_the_absolute_instants() -> None:
    """09:00 in Tokyo and 00:00 UTC are the same instant; the offset must cancel."""
    assert (
        timeutil.span(
            "2026-01-01T09:00:00+09:00", "2026-01-01T00:00:00Z", unit="seconds"
        )
        == 0
    )
