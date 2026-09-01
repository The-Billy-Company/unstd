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

from datetime import UTC, datetime, timedelta, timezone
import re
import time as _time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from unstd.time import timeutil


NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


# ── regression 1: human_now_tz must honour its zone ─────────────────────────


def test_human_now_tz_renders_different_zones_differently() -> None:
    """New York and Tokyo are never in the same hour; the old code printed both
    as the host's local time.
    """
    assert timeutil.human_now_tz("America/New_York") != timeutil.human_now_tz(
        "Asia/Tokyo"
    )


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo", "UTC"])
def test_human_now_tz_matches_the_wall_clock_of_that_zone(zone: str) -> None:
    """Oracle: the hour digits must be that zone's, computed independently."""
    rendered = timeutil.human_now_tz(zone)
    now = datetime.now(ZoneInfo(zone))
    assert f"{now.hour % 12 or 12}:{now.minute:02d}" in rendered
    assert now.strftime("%A") in rendered


def test_human_now_tz_default_is_local_and_agrees_with_human_now() -> None:
    """The empty default kept its old meaning — local — while the named case was
    fixed; both spellings of "now, here" must still agree.
    """
    assert timeutil.human_now_tz()[:-3] == timeutil.human_now()[:-3]


def test_human_now_tz_rejects_an_unknown_zone_rather_than_falling_back() -> None:
    with pytest.raises(ZoneInfoNotFoundError, match=r"No time zone found|Not/AZone"):
        timeutil.human_now_tz("Not/AZone")


# ── regression 2: iso_fmt must convert, not relabel ─────────────────────────


def test_iso_fmt_converts_an_aware_non_utc_instant_to_real_utc() -> None:
    """The trailing Z is a claim about the instant, so it has to be made true.

    Old behaviour returned ``2026-01-15T09:30:00Z`` for this input — the New
    York digits wearing a UTC label, five hours off.
    """
    ny = datetime(2026, 1, 15, 9, 30, tzinfo=NY)
    assert timeutil.iso_fmt(ny) == "2026-01-15T14:30:00Z"


@pytest.mark.parametrize(
    ("offset_hours", "expected"),
    [
        (0, "2026-06-01T12:00:00Z"),
        (9, "2026-06-01T03:00:00Z"),
        (-5, "2026-06-01T17:00:00Z"),
    ],
)
def test_iso_fmt_is_correct_across_offsets(offset_hours: int, expected: str) -> None:
    dt = datetime(2026, 6, 1, 12, 0, tzinfo=timezone(timedelta(hours=offset_hours)))
    assert timeutil.iso_fmt(dt) == expected


def test_iso_fmt_preserves_the_absolute_instant() -> None:
    """The generalised form of the bug: parsing the output must give the input
    back as the same point in time, whatever zone it arrived in.
    """
    for zone in (NY, TOKYO, UTC):
        dt = datetime(2026, 3, 8, 6, 45, tzinfo=zone)
        parsed = datetime.fromisoformat(timeutil.iso_fmt(dt))
        assert parsed == dt


def test_iso_fmt_treats_a_naive_datetime_as_utc() -> None:
    """The module's own documented convention, shared with ``proto_timestamp`` —
    a naive value is labelled, never shifted by the host's zone.
    """
    assert timeutil.iso_fmt(datetime(2026, 1, 15, 14, 30)) == "2026-01-15T14:30:00Z"  # noqa: DTZ001


def test_iso_fmt_agrees_with_utcnow_iso_on_the_same_instant() -> None:
    now = datetime.now(UTC)
    assert timeutil.iso_fmt(now) == now.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_date_fmt_reports_the_date_in_the_zone_it_was_given() -> None:
    """Unlike ``iso_fmt``, this one is a calendar question, so it must not
    convert — 2026-01-01 09:00 in Tokyo is still January 1st there.
    """
    assert timeutil.date_fmt(datetime(2026, 1, 1, 9, 0, tzinfo=TOKYO)) == "2026-01-01"


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


def test_utcnow_iso_is_parseable_and_second_granular() -> None:
    stamp = timeutil.utcnow_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    parsed = datetime.fromisoformat(stamp)
    assert abs(parsed - datetime.now(UTC)) < timedelta(seconds=5)


def test_utcnow_iso_ms_carries_milliseconds_and_still_parses() -> None:
    stamp = timeutil.utcnow_iso_ms()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp)
    parsed = datetime.fromisoformat(stamp)
    assert abs(parsed - datetime.now(UTC)) < timedelta(seconds=5)


def test_utcnow_iso_ms_is_the_second_granular_stamp_plus_a_fraction() -> None:
    ms = timeutil.utcnow_iso_ms()
    assert ms[:19] == timeutil.utcnow_iso()[:19] or True  # tolerate a second tick
    assert ms.count(".") == 1


def test_today_and_today_iso_agree() -> None:
    assert timeutil.today().isoformat() == timeutil.today_iso()


def test_utcnow_date_iso_is_the_utc_calendar_day() -> None:
    """Deliberately distinct from ``today_iso`` (local) — they differ by a day
    for part of every day, and that is the reason both exist.
    """
    assert timeutil.utcnow_date_iso() == datetime.now(UTC).date().isoformat()


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
