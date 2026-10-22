"""Generated ``unstd.time.zoned`` resolution laws against stdlib ``zoneinfo``.

Walls are drawn within two hours of hand-picked transition instants, so gaps,
folds and ordinary times all occur. The oracle classifies each wall from
PEP 495 offsets alone: its two candidate instants are ``wall - offset`` for
``fold=0`` and ``fold=1``, and a candidate is real iff ``zoneinfo`` maps it back
to the same wall. Two real candidates are a fold, none is a gap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import example, given
from hypothesis import strategies as st
import pytest
from whenever import RepeatedTime, SkippedTime

from unstd.time import zoned


# (zone, UTC instant of a transition) — published tzdb facts.
TRANSITIONS = [
    ("America/New_York", datetime(2026, 3, 8, 7, tzinfo=UTC)),
    ("America/New_York", datetime(2026, 11, 1, 6, tzinfo=UTC)),
    ("America/New_York", datetime(1883, 11, 18, 17, tzinfo=UTC)),
    ("Europe/London", datetime(2026, 3, 29, 1, tzinfo=UTC)),
    ("Europe/London", datetime(2026, 10, 25, 1, tzinfo=UTC)),
    ("Europe/London", datetime(1847, 12, 1, 0, 1, 15, tzinfo=UTC)),
    ("Australia/Sydney", datetime(2026, 4, 4, 16, tzinfo=UTC)),
    ("Australia/Sydney", datetime(2026, 10, 3, 16, tzinfo=UTC)),
    ("Australia/Lord_Howe", datetime(2026, 4, 4, 15, tzinfo=UTC)),
    ("Australia/Lord_Howe", datetime(2026, 10, 3, 15, 30, tzinfo=UTC)),
    ("America/St_Johns", datetime(2026, 3, 8, 5, 30, tzinfo=UTC)),
    ("Asia/Kathmandu", datetime(1985, 12, 31, 18, 30, tzinfo=UTC)),
]
MODES = ("compatible", "earlier", "later")


@st.composite
def walls(draw: st.DrawFn) -> tuple[str, datetime]:
    zone, instant = draw(st.sampled_from(TRANSITIONS))
    # The last pre-transition wall second, plus one: where the gap or fold opens.
    last = (
        (instant - timedelta(seconds=1)).astimezone(ZoneInfo(zone)).replace(tzinfo=None)
    )
    anchor = (last + timedelta(seconds=1)).replace(second=0)
    minutes = draw(st.one_of(st.integers(-61, 61), st.integers(-120, 120)))
    return zone, anchor + timedelta(minutes=minutes)


def _expected(zone: str, wall: datetime) -> tuple[str, datetime, datetime]:
    tz = ZoneInfo(zone)
    offsets = {wall.replace(tzinfo=tz, fold=fold).utcoffset() for fold in (0, 1)}
    candidates = sorted(
        (wall - offset).replace(tzinfo=UTC) for offset in offsets if offset is not None
    )
    real = [c for c in candidates if c.astimezone(tz).replace(tzinfo=None) == wall]
    if len(real) == len(candidates) == 2:
        return "fold", real[0], real[1]
    if not real:
        return "gap", candidates[0], candidates[-1]
    return "unique", real[0], real[0]


@given(walls())
@example(("America/New_York", datetime(2026, 11, 1, 1, 30)))  # noqa: DTZ001
@example(("Australia/Lord_Howe", datetime(2026, 4, 5, 1, 45)))  # noqa: DTZ001
@example(("America/New_York", datetime(1883, 11, 18, 12, 3)))  # noqa: DTZ001
@example(("Asia/Kathmandu", datetime(1986, 1, 1, 0, 14)))  # noqa: DTZ001
def test_wall_resolution_matches_zoneinfo(case: tuple[str, datetime]) -> None:
    zone, wall = case
    kind, earlier, later = _expected(zone, wall)
    picks = {
        "compatible": earlier if kind == "fold" else later,
        "earlier": earlier,
        "later": later,
    }
    fields = (wall.year, wall.month, wall.day, wall.hour, wall.minute)
    for mode in MODES:
        for resolved in (
            zoned.wall(*fields, zone, disambiguate=mode),  # type: ignore[arg-type]
            zoned.parse(wall.isoformat(), zone, disambiguate=mode),  # type: ignore[arg-type]
        ):
            assert resolved is not None
            assert zoned.to_utc(resolved) == picks[mode]
            assert (
                resolved.to_stdlib().utcoffset()
                == picks[mode].astimezone(ZoneInfo(zone)).utcoffset()
            )
    if kind == "unique":
        assert zoned.wall(*fields, zone, disambiguate="raise") is not None
    else:
        with pytest.raises(RepeatedTime if kind == "fold" else SkippedTime):
            zoned.wall(*fields, zone, disambiguate="raise")


@given(
    st.datetimes(
        min_value=datetime(1800, 1, 1),  # noqa: DTZ001 - bounds for an aware strategy
        max_value=datetime(2200, 1, 1),  # noqa: DTZ001
        timezones=st.just(UTC),
    ),
    st.sampled_from(sorted({zone for zone, _ in TRANSITIONS})),
)
def test_aware_instants_localize_to_the_zoneinfo_wall(
    instant: datetime, zone: str
) -> None:
    local = instant.astimezone(ZoneInfo(zone))
    resolved = zoned.parse(instant.isoformat(), zone, disambiguate="raise")
    assert resolved is not None
    assert resolved.tz == zone
    assert zoned.to_utc(resolved) == instant
    assert (
        resolved.year,
        resolved.month,
        resolved.day,
        resolved.hour,
        resolved.minute,
        resolved.second,
    ) == (
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
    )
    assert resolved.to_stdlib().utcoffset() == local.utcoffset()
    inst = zoned.instant(local.isoformat())
    assert inst is not None
    assert inst.to_stdlib() == instant
    assert zoned.instant(instant.replace(tzinfo=None).isoformat()) is None
    floating = zoned.instant(instant.replace(tzinfo=None).isoformat(), assume_utc=True)
    assert floating is not None
    assert floating.to_stdlib() == instant
