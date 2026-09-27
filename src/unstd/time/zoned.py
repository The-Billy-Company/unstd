"""Zoned wall-clock time — DST-correct local times over ``whenever``.

Absolute-instant work (parsing, formatting, durations) is pure stdlib and lives
in :mod:`unstd.time.timeutil`. What stdlib gets *wrong* is wall-clock time in a
zone: ``datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York"))``
constructs a time that never happened, with an offset nobody asked for, and says
nothing. This module is where a local time is resolved against its zone's DST
rules explicitly::

    from unstd.time import zoned

    zoned.wall(2026, 3, 8, 2, 30, "America/New_York")  # gap → shifted to 3:30 EDT
    zoned.parse("2026-11-01T01:30", "America/New_York", disambiguate="later")
    zoned.to_utc(z)  # aware-UTC stdlib datetime

Every function that resolves a wall time takes *disambiguate*, whenever's own
fold/gap strategy: ``"compatible"`` (the RFC 5545 default — shift a
spring-forward gap forward, take the earlier offset on a fall-back fold),
``"earlier"`` / ``"later"`` to pick a side, or ``"raise"`` to fail loud with
``RepeatedTime`` / ``SkippedTime`` (both ``ValueError``).

The ``whenever`` types these return are re-exported, so a caller annotates with
``zoned.ZonedDateTime`` without importing ``whenever`` beside the seam.

Backend: there is no faithful stdlib substitute for DST-correct resolution, so
this module hard-requires the ``time`` extra and raises an ImportError naming it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from unstd.time.timeutil import parse_iso


if TYPE_CHECKING:
    from datetime import datetime

try:
    from whenever import (
        Instant,
        PlainDateTime,
        RepeatedTime,
        SkippedTime,
        ZonedDateTime,
    )
except ImportError as exc:  # DST-correct resolution has no faithful stdlib substitute
    msg = "unstd.time.zoned requires the 'time' extra — install with: pip install 'unstd[time]'"
    raise ImportError(msg) from exc


__all__ = [
    "Disambiguate",
    "Instant",
    "PlainDateTime",
    "RepeatedTime",
    "SkippedTime",
    "ZonedDateTime",
    "instant",
    "now",
    "parse",
    "to_utc",
    "wall",
]

type Disambiguate = Literal["compatible", "raise", "earlier", "later"]


now = ZonedDateTime.now
"""The current instant in IANA *zone* — the seam a test pins to make relative resolution (an alarm's day-roll) deterministic."""


def instant(raw: str, *, assume_utc: bool = False) -> Instant | None:
    """The absolute ``Instant`` an ISO/RFC-3339 *raw* names, or ``None`` when it names none.

    A floating value (no offset, or date-only) names no instant, so it is
    ``None`` unless ``assume_utc`` reads it as UTC.
    """
    dt = parse_iso(raw)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return Instant(dt)
    return PlainDateTime(dt).assume_utc() if assume_utc else None


def parse(
    raw: str, zone: str, *, disambiguate: Disambiguate = "compatible"
) -> ZonedDateTime | None:
    """ISO/RFC-3339 *raw* localized to *zone*, or ``None`` when unparseable.

    An aware value keeps its absolute instant (converted into the zone, where
    *disambiguate* is moot — an instant has no fold or gap). A floating value is
    read as local wall time in *zone*, and *disambiguate* resolves its fold/gap.
    ``"raise"`` propagates rather than collapsing into ``None``.
    """
    dt = parse_iso(raw)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return Instant(dt).to_tz(zone)
    return PlainDateTime(dt).assume_tz(zone, disambiguate=disambiguate)


def wall(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    zone: str,
    *,
    disambiguate: Disambiguate = "compatible",
) -> ZonedDateTime | None:
    """A local wall-clock time in *zone*, resolved DST-correctly — or ``None`` for invalid fields or zone.

    ``disambiguate="raise"``'s ``RepeatedTime`` / ``SkippedTime`` propagate
    rather than collapsing into ``None``: that exception *is* the answer the
    caller asked for.
    """
    try:
        return ZonedDateTime(
            year, month, day, hour, minute, tz=zone, disambiguate=disambiguate
        )
    except (RepeatedTime, SkippedTime):
        raise
    except ValueError:
        return None


def to_utc(z: ZonedDateTime) -> datetime:
    """The absolute instant of *z* as an aware-UTC stdlib ``datetime``."""
    return z.to_instant().to_stdlib()
