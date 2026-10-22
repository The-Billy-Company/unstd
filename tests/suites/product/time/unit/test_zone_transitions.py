"""Hand-authored IANA transition families for ``unstd.time.zoned``.

Each row is a published tzdb fact, written as the UTC instant a local wall time
must resolve to. Families: northern spring gaps and autumn folds (New York,
London), the southern hemisphere (Sydney), a 30-minute DST shift (Lord Howe), a
half-hour standard zone (St. John's), a 15-minute standard-offset change
(Kathmandu's 1986 move from +05:30 to +05:45), and non-hour local-mean-time
offsets (New York's 1883 -04:56:02 and London's 1847 -00:01:15).

``compatible`` follows RFC 5545: a fold takes the earlier offset and a gap shifts
forward. Stdlib ``zoneinfo`` is checked against the same table first, so a
wrong fixture fails before any product assertion runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from whenever import (
    Instant,
    RepeatedTime,
    SkippedTime,
    TimeZoneNotFoundError,
    patch_current_time,
)

from unstd.time import zoned


def _utc(*fields: int) -> datetime:
    return datetime(*fields, tzinfo=UTC)  # type: ignore[misc]


def _offset(hours: int, minutes: int = 0, seconds: int = 0) -> timedelta:
    sign = -1 if hours < 0 else 1
    return sign * timedelta(hours=abs(hours), minutes=minutes, seconds=seconds)


# (zone, wall fields, kind, earlier instant, later instant, offset before, offset after)
TRANSITIONS = [
    (
        "America/New_York",
        (2026, 3, 8, 2, 30),
        "gap",
        _utc(2026, 3, 8, 6, 30),
        _utc(2026, 3, 8, 7, 30),
        _offset(-5),
        _offset(-4),
    ),
    (
        "America/New_York",
        (2026, 11, 1, 1, 30),
        "fold",
        _utc(2026, 11, 1, 5, 30),
        _utc(2026, 11, 1, 6, 30),
        _offset(-4),
        _offset(-5),
    ),
    (
        "Europe/London",
        (2026, 3, 29, 1, 30),
        "gap",
        _utc(2026, 3, 29, 0, 30),
        _utc(2026, 3, 29, 1, 30),
        _offset(0),
        _offset(1),
    ),
    (
        "Europe/London",
        (2026, 10, 25, 1, 30),
        "fold",
        _utc(2026, 10, 25, 0, 30),
        _utc(2026, 10, 25, 1, 30),
        _offset(1),
        _offset(0),
    ),
    (
        "Australia/Sydney",
        (2026, 4, 5, 2, 30),
        "fold",
        _utc(2026, 4, 4, 15, 30),
        _utc(2026, 4, 4, 16, 30),
        _offset(11),
        _offset(10),
    ),
    (
        "Australia/Sydney",
        (2026, 10, 4, 2, 30),
        "gap",
        _utc(2026, 10, 3, 15, 30),
        _utc(2026, 10, 3, 16, 30),
        _offset(10),
        _offset(11),
    ),
    (
        "Australia/Lord_Howe",
        (2026, 4, 5, 1, 45),
        "fold",
        _utc(2026, 4, 4, 14, 45),
        _utc(2026, 4, 4, 15, 15),
        _offset(11),
        _offset(10, 30),
    ),
    (
        "Australia/Lord_Howe",
        (2026, 10, 4, 2, 15),
        "gap",
        _utc(2026, 10, 3, 15, 15),
        _utc(2026, 10, 3, 15, 45),
        _offset(10, 30),
        _offset(11),
    ),
    (
        "America/St_Johns",
        (2026, 3, 8, 2, 30),
        "gap",
        _utc(2026, 3, 8, 5, 0),
        _utc(2026, 3, 8, 6, 0),
        _offset(-3, 30),
        _offset(-2, 30),
    ),
    (
        "Asia/Kathmandu",
        (1986, 1, 1, 0, 5),
        "gap",
        _utc(1985, 12, 31, 18, 20),
        _utc(1985, 12, 31, 18, 35),
        _offset(5, 30),
        _offset(5, 45),
    ),
    (
        "America/New_York",
        (1883, 11, 18, 12, 0),
        "fold",
        _utc(1883, 11, 18, 16, 56, 2),
        _utc(1883, 11, 18, 17, 0),
        _offset(-4, 56, 2),
        _offset(-5),
    ),
    (
        "Europe/London",
        (1847, 12, 1, 0, 1),
        "gap",
        _utc(1847, 12, 1, 0, 1),
        _utc(1847, 12, 1, 0, 2, 15),
        -timedelta(minutes=1, seconds=15),
        _offset(0),
    ),
]
IDS = [f"{row[0]}-{row[1][0]}-{row[2]}" for row in TRANSITIONS]


@pytest.mark.parametrize(
    ("zone", "fields", "kind", "earlier", "later", "before", "after"),
    TRANSITIONS,
    ids=IDS,
)
def test_fixture_agrees_with_zoneinfo(
    zone: str,
    fields: tuple[int, ...],
    kind: str,
    earlier: datetime,
    later: datetime,
    before: timedelta,
    after: timedelta,
) -> None:
    tz = ZoneInfo(zone)
    wall = datetime(*fields)  # type: ignore[misc]  # noqa: DTZ001 - local wall fields
    # PEP 495: fold=0 reads the pre-transition offset in both a gap and a fold.
    assert wall.replace(tzinfo=tz).utcoffset() == before
    assert wall.replace(tzinfo=tz, fold=1).utcoffset() == after
    assert {wall - before, wall - after} == {
        earlier.replace(tzinfo=None),
        later.replace(tzinfo=None),
    }
    round_trips = {
        instant.astimezone(tz).replace(tzinfo=None) == wall
        for instant in (earlier, later)
    }
    assert round_trips == ({True} if kind == "fold" else {False})


@pytest.mark.parametrize(
    ("zone", "fields", "kind", "earlier", "later", "before", "after"),
    TRANSITIONS,
    ids=IDS,
)
def test_wall_and_parse_resolve_each_transition(
    zone: str,
    fields: tuple[int, int, int, int, int],
    kind: str,
    earlier: datetime,
    later: datetime,
    before: timedelta,
    after: timedelta,
) -> None:
    tz = ZoneInfo(zone)
    raw = datetime(*fields).isoformat()  # noqa: DTZ001 - local wall fields
    expected = {
        "compatible": earlier if kind == "fold" else later,
        "earlier": earlier,
        "later": later,
    }
    for mode, instant in expected.items():
        for resolved in (
            zoned.wall(*fields, zone, disambiguate=mode),  # type: ignore[arg-type]
            zoned.parse(raw, zone, disambiguate=mode),  # type: ignore[arg-type]
        ):
            assert resolved is not None
            assert resolved.tz == zone
            utc = zoned.to_utc(resolved)
            assert utc == instant
            assert utc.tzinfo is UTC
            local = instant.astimezone(tz)
            assert (resolved.hour, resolved.minute, resolved.second) == (
                local.hour,
                local.minute,
                local.second,
            )
            assert resolved.to_stdlib().utcoffset() == local.utcoffset()
    error = RepeatedTime if kind == "fold" else SkippedTime
    with pytest.raises(error):
        zoned.wall(*fields, zone, disambiguate="raise")
    with pytest.raises(error):
        zoned.parse(raw, zone, disambiguate="raise")


@pytest.mark.parametrize(
    ("zone", "fields", "kind", "earlier", "later", "before", "after"),
    TRANSITIONS,
    ids=IDS,
)
def test_aware_input_keeps_its_instant_on_either_side(
    zone: str,
    fields: tuple[int, ...],
    kind: str,
    earlier: datetime,
    later: datetime,
    before: timedelta,
    after: timedelta,
) -> None:
    for instant in (earlier, later):
        for mode in ("compatible", "earlier", "later", "raise"):
            resolved = zoned.parse(instant.isoformat(), zone, disambiguate=mode)  # type: ignore[arg-type]
            assert resolved is not None
            assert zoned.to_utc(resolved) == instant
        assert zoned.instant(instant.isoformat()) == Instant.from_utc(
            instant.year,
            instant.month,
            instant.day,
            instant.hour,
            instant.minute,
            instant.second,
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-08T13:15:00+05:45", Instant.from_utc(2026, 3, 8, 7, 30)),
        ("2026-03-08T21:15:00+13:45", Instant.from_utc(2026, 3, 8, 7, 30)),
        ("1883-11-18T12:00:00-04:56:02", Instant.from_utc(1883, 11, 18, 16, 56, 2)),
        (
            "2026-01-01T00:00:00.123456789Z",
            Instant.from_utc(2026, 1, 1, nanosecond=123_456_000),
        ),
        ("0001-01-01T00:00:00Z", Instant.from_utc(1, 1, 1)),
        (
            "9999-12-31T23:59:59.999999Z",
            Instant.from_utc(9999, 12, 31, 23, 59, 59, nanosecond=999_999_000),
        ),
    ],
)
def test_instant_reads_odd_offsets_bounds_and_truncated_fractions(
    raw: str, expected: Instant
) -> None:
    assert zoned.instant(raw) == expected


@pytest.mark.parametrize(
    ("fields", "zone", "utc"),
    [
        ((1, 1, 1, 0, 0), "America/New_York", _utc(1, 1, 1, 4, 56, 2)),
        ((9999, 12, 31, 23, 59), "Asia/Tokyo", _utc(9999, 12, 31, 14, 59)),
        ((1, 1, 1, 0, 0), "Asia/Tokyo", None),
        ((9999, 12, 31, 23, 59), "America/New_York", None),
    ],
    ids=["year-1-lmt", "year-9999", "before-year-1", "after-year-9999"],
)
def test_wall_at_the_calendar_limits(
    fields: tuple[int, int, int, int, int], zone: str, utc: datetime | None
) -> None:
    resolved = zoned.wall(*fields, zone)
    assert (resolved and zoned.to_utc(resolved)) == utc


def test_now_reads_a_pinned_instant_in_each_family() -> None:
    with patch_current_time(Instant.from_utc(2026, 3, 8, 7, 30), keep_ticking=False):
        observed = {
            zone: (z.hour, z.minute, z.to_stdlib().utcoffset())
            for zone in (
                "America/New_York",
                "America/St_Johns",
                "Asia/Kathmandu",
                "Australia/Lord_Howe",
            )
            if (z := zoned.now(zone)).tz == zone
        }
    assert observed == {
        "America/New_York": (3, 30, _offset(-4)),
        "America/St_Johns": (5, 0, _offset(-2, 30)),
        "Asia/Kathmandu": (13, 15, _offset(5, 45)),
        "Australia/Lord_Howe": (18, 30, _offset(11)),
    }


def test_unknown_zone_policy_differs_by_entry_point() -> None:
    assert zoned.wall(2026, 1, 1, 0, 0, "Not/AZone") is None
    with pytest.raises(TimeZoneNotFoundError):
        zoned.parse("2026-01-01T00:00", "Not/AZone")
