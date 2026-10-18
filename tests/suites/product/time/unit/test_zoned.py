"""Tests for ``unstd.time.zoned`` — DST-correct wall-clock time over ``whenever``.

Covers instant parsing and — the reason this suite exists — ``disambiguate=`` on
``wall``/``parse`` across a real DST fold (fall-back) and gap (spring-forward) in
Europe/London.
"""

from __future__ import annotations

from datetime import UTC

import pytest
from whenever import Instant, RepeatedTime, SkippedTime, ZonedDateTime

from unstd.time import zoned


# Europe/London: fall-back fold on 2023-10-29 (01:00-02:00 local occurs twice),
# spring-forward gap on 2023-03-26 (01:00-02:00 local does not exist).
FOLD_ZONE = "Europe/London"
FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN = 2023, 10, 29, 1, 15
GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN = 2023, 3, 26, 1, 30


# ── instant ──────────────────────────────────────────────────────────────────


def test_instant_aware() -> None:
    inst = zoned.instant("2026-01-01T00:00:00+00:00")
    assert isinstance(inst, Instant)


def test_instant_naive_without_assume_utc_is_none() -> None:
    assert zoned.instant("2026-01-01T00:00:00") is None


def test_instant_naive_with_assume_utc() -> None:
    inst = zoned.instant("2026-01-01T00:00:00", assume_utc=True)
    assert isinstance(inst, Instant)


def test_instant_unparseable_is_none() -> None:
    assert zoned.instant("garbage") is None


# ── now ────────────────────────────────────────────────────────────────


def test_now_is_zoned_datetime_in_requested_zone() -> None:
    z = zoned.now("America/New_York")
    assert isinstance(z, ZonedDateTime)
    assert z.tz == "America/New_York"


# ── wall: baseline + disambiguate= ────────────────────────────────────


def test_wall_unambiguous() -> None:
    z = zoned.wall(2026, 6, 15, 12, 0, "Europe/London")
    assert z is not None
    assert z.hour == 12


def test_wall_invalid_fields_is_none() -> None:
    assert zoned.wall(2026, 13, 1, 0, 0, "Europe/London") is None


def test_wall_invalid_zone_is_none() -> None:
    assert zoned.wall(2026, 1, 1, 0, 0, "Not/AZone") is None


def test_wall_default_disambiguate_is_compatible_on_fold() -> None:
    z = zoned.wall(FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE)
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


def test_wall_disambiguate_earlier_vs_later_on_fold() -> None:
    earlier = zoned.wall(
        FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="earlier"
    )
    later = zoned.wall(
        FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="later"
    )
    assert earlier is not None
    assert later is not None
    assert earlier.to_instant() != later.to_instant()
    assert earlier.to_instant() < later.to_instant()


def test_wall_disambiguate_raise_propagates_on_fold() -> None:
    with pytest.raises(RepeatedTime):
        zoned.wall(
            FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE, disambiguate="raise"
        )


def test_wall_disambiguate_raise_propagates_on_gap() -> None:
    with pytest.raises(SkippedTime):
        zoned.wall(GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN, FOLD_ZONE, disambiguate="raise")


def test_wall_disambiguate_raise_does_not_collapse_to_none_when_fields_are_valid() -> (
    None
):
    """A ``RepeatedTime``/``SkippedTime`` is a ``ValueError`` subclass — the
    fields/zone themselves are perfectly valid, so it must not be swallowed by
    the same ``except ValueError: return None`` that handles genuinely bad
    input (e.g. ``month=13``).
    """
    with pytest.raises(ValueError, match="skipped"):
        zoned.wall(GAP_Y, GAP_M, GAP_D, GAP_H, GAP_MIN, FOLD_ZONE, disambiguate="raise")


# ── parse: aware passthrough + naive disambiguate= ───────────────────


def test_parse_aware_converts_into_zone() -> None:
    z = zoned.parse("2026-01-01T00:00:00+00:00", "America/New_York")
    assert z is not None
    assert z.tz == "America/New_York"
    assert z.to_instant() == Instant.from_utc(2026, 1, 1)


def test_parse_naive_reads_as_local_wall_time() -> None:
    z = zoned.parse("2026-06-15T12:00:00", "Europe/London")
    assert z is not None
    assert z.hour == 12


def test_parse_unparseable_is_none() -> None:
    assert zoned.parse("garbage", "Europe/London") is None


def test_parse_default_disambiguate_matches_wall_on_fold() -> None:
    raw = f"{FOLD_Y:04d}-{FOLD_M:02d}-{FOLD_D:02d}T{FOLD_H:02d}:{FOLD_MIN:02d}:00"
    via_parse = zoned.parse(raw, FOLD_ZONE)
    via_wall = zoned.wall(FOLD_Y, FOLD_M, FOLD_D, FOLD_H, FOLD_MIN, FOLD_ZONE)
    assert via_parse == via_wall


def test_parse_disambiguate_raise_propagates_on_fold() -> None:
    raw = f"{FOLD_Y:04d}-{FOLD_M:02d}-{FOLD_D:02d}T{FOLD_H:02d}:{FOLD_MIN:02d}:00"
    with pytest.raises(RepeatedTime):
        zoned.parse(raw, FOLD_ZONE, disambiguate="raise")


def test_parse_disambiguate_moot_for_aware_input() -> None:
    """An aware ``raw`` carries its own absolute instant — no fold/gap to
    resolve — so ``disambiguate`` must not affect (or be rejected for) it.
    """
    z = zoned.parse("2026-01-01T00:00:00+00:00", "Europe/London", disambiguate="raise")
    assert z is not None


# ── to_utc ───────────────────────────────────────────────────────────────────


def test_to_utc() -> None:
    z = zoned.wall(2026, 6, 15, 12, 0, "Europe/London")
    assert z is not None
    utc = zoned.to_utc(z)
    assert utc.tzinfo is UTC
    assert utc.hour == 11  # BST is UTC+1 in June
