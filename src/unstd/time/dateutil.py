"""Date parsing, math, and range utilities.

Partner module to :mod:`unstd.time.timeutil` — handles ISO parsing (no Z→+00:00
hack needed on Python 3.11+), duration math, and calendar range helpers.

The ``parse_instant`` / ``span_hours`` / ``age_days`` trio is the typed-time
seam: callers doing arithmetic across an ISO/RFC3339 boundary route through it
instead of subtracting raw ``parse_iso`` results, which silently mix a naive
(date-only / offset-less) value with an aware ``now`` and raise ``TypeError``.
``whenever`` is kept *here* so the absolute-instant handling lives in one place
rather than scattered across every time-sensitive module.

Backend: the typed-instant seam *is* ``whenever`` — there is no
faithful stdlib substitute for its DST-correct absolute-instant math — so this
module hard-requires the ``time`` extra. Imported without it, it raises a clear
ImportError pointing at ``pip install 'unstd[time]'``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Literal


try:
    from whenever import (
        Instant,
        PlainDateTime,
        RepeatedTime,
        SkippedTime,
        ZonedDateTime,
    )
except ImportError as exc:  # the typed-instant seam has no faithful stdlib substitute
    msg = "unstd.time.dateutil requires the 'time' extra — install with: pip install 'unstd[time]'"
    raise ImportError(msg) from exc

# whenever's own DST-fold/gap resolution strategies, re-exported so a caller
# never has to import `whenever` directly just to spell one of these four.
Disambiguate = Literal["compatible", "raise", "earlier", "later"]


def parse_iso(raw: str) -> datetime | None:
    """Parse iso."""
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def parse_instant(raw: str, *, assume_utc: bool = False) -> Instant | None:
    """Absolute instant for an ISO/RFC3339 timestamp, or ``None`` when *raw* carries no usable instant — a date-only value ("2026-06-30") or a floating/naive datetime ("…T14:00" with no offset).

    With ``assume_utc`` a naive value is read as UTC (the ``proto_timestamp``
    empty-tz contract), so a legacy tz-less timestamp still yields a comparable
    instant instead of crashing aware/naive arithmetic. Without it, only an
    aware value resolves — the caller skips the un-anchorable boundary.
    """
    dt = parse_iso(raw)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return Instant(dt)
    return PlainDateTime(dt).assume_utc() if assume_utc else None


def span_hours(start_raw: str, end_raw: str) -> float | None:
    """Hours between two ISO/RFC3339 boundaries (``end - start``), or ``None`` when either lacks a parseable datetime.

    Both boundaries are read as UTC when floating: an event's own offset cancels
    in the subtraction, so a tz-less event still yields its true duration rather
    than raising on a naive/aware mix.
    """
    s = parse_instant(start_raw, assume_utc=True)
    e = parse_instant(end_raw, assume_utc=True)
    return None if s is None or e is None else (e - s).total("hours")


def age_days(raw: str) -> float | None:
    """Days from an absolute timestamp *raw* to now (UTC), or ``None`` when unparseable. Naive values are read as UTC; the result is negative for a future timestamp, so callers clamp when they only want elapsed age."""
    inst = parse_instant(raw, assume_utc=True)
    return None if inst is None else (Instant.now() - inst).total("hours") / 24.0


def parse_utc(raw: str) -> datetime | None:
    """Absolute instant for *raw* as an aware-UTC stdlib ``datetime`` (a naive value read as UTC, matching ``proto_timestamp``), or ``None`` when *raw* carries no parseable datetime — the caller skips an un-anchorable boundary instead of crashing an aware/naive compare."""
    inst = parse_instant(raw, assume_utc=True)
    return None if inst is None else inst.to_stdlib()


def minutes_until(raw: str, now: datetime) -> float | None:
    """Signed minutes from aware *now* to the instant in *raw* (``raw - now``); ``None`` when *raw* has no usable (aware) instant. Strict — a floating/naive or date-only value is un-anchorable and skipped, never assumed UTC."""
    inst = parse_instant(raw)
    return None if inst is None else (inst - Instant(now)).total("minutes")


def minutes_since(raw: str, now: datetime) -> float | None:
    """Minutes elapsed from the instant in *raw* to aware *now* (``now - raw``); ``None`` when *raw* has no usable (aware) instant."""
    inst = parse_instant(raw)
    return None if inst is None else (Instant(now) - inst).total("minutes")


def span_minutes(start_raw: str, end_raw: str) -> float | None:
    """Minutes between two aware instants (``end - start``); ``None`` when either boundary lacks a usable instant. Strict (aware-only), unlike ``span_hours`` which reads a floating boundary as UTC."""
    start, end = parse_instant(start_raw), parse_instant(end_raw)
    return None if start is None or end is None else (end - start).total("minutes")


def zoned_now(zone: str) -> ZonedDateTime:
    """Return the current instant in IANA *zone* — the wall-clock seam so relative resolution (e.g. an alarm's day-roll) stays deterministic under test."""
    return ZonedDateTime.now(zone)


def parse_zoned(
    raw: str, zone: str, *, disambiguate: Disambiguate = "compatible"
) -> ZonedDateTime | None:
    """ISO/RFC3339 *raw* localized to *zone*: an aware value keeps its absolute instant (converted into the zone, where *disambiguate* is moot — an instant has no fold/gap), a floating/naive value is read as local wall time (where *disambiguate* resolves a DST fold/gap exactly as in :func:`zoned_wall`). ``None`` when *raw* carries no parseable datetime; ``disambiguate="raise"`` still raises through (``RepeatedTime``/``SkippedTime``, both ``ValueError`` subclasses) rather than being swallowed into ``None``."""
    dt = parse_iso(raw)
    if dt is None:
        return None
    return (
        Instant(dt).to_tz(zone)
        if dt.tzinfo is not None
        else PlainDateTime(dt).assume_tz(zone, disambiguate=disambiguate)
    )


def zoned_wall(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    zone: str,
    *,
    disambiguate: Disambiguate = "compatible",
) -> ZonedDateTime | None:
    """Return a local wall-clock time as a DST-correct zoned instant. The default ``"compatible"`` shifts a spring-forward gap forward and picks the earlier offset on a fall-back fold (RFC 5545 semantics), instead of the wrong-offset silent bug of stdlib ``datetime(..., tzinfo=zone)``; pass ``"earlier"``/``"later"`` to pick a side explicitly, or ``"raise"`` to fail loud on an ambiguous/skipped wall time instead of silently picking one. ``None`` when the fields or *zone* are invalid — but ``disambiguate="raise"``'s own ``RepeatedTime``/``SkippedTime`` (both ``ValueError`` subclasses) propagate rather than collapsing into that ``None``, since that exception *is* the answer the caller asked for."""
    try:
        return ZonedDateTime(
            year, month, day, hour, minute, tz=zone, disambiguate=disambiguate
        )
    except (RepeatedTime, SkippedTime):
        raise
    except ValueError:
        return None


def zoned_to_utc(z: ZonedDateTime) -> datetime:
    """Return the absolute instant of *z* as an aware-UTC stdlib ``datetime``."""
    return z.to_instant().to_stdlib()


def hours_between(start: datetime, end: datetime) -> float:
    """Hours between."""
    return round((end - start).total_seconds() / 3600, 2)


def iso_range(start: datetime, end: datetime) -> tuple[str, str]:
    """Iso range."""
    from unstd.time.timeutil import iso_fmt

    return iso_fmt(start), iso_fmt(end)


def lookback(hours: int = 24) -> tuple[datetime, datetime]:
    """Lookback."""
    now = datetime.now(UTC)
    return now - timedelta(hours=hours), now


def days_range(days: int) -> tuple[date, date]:
    """Return ``(today - days, today)`` as UTC calendar dates."""
    today = datetime.now(UTC).date()
    return today - timedelta(days=days), today


def day_offset(base: date, days: int) -> date:
    """Day offset."""
    return base + timedelta(days=days)


def parse_date(raw: str) -> date:
    """Parse date."""
    return date.fromisoformat(raw)


def from_epoch_s(s: float, tz: tzinfo = UTC) -> datetime:
    """From epoch s."""
    return datetime.fromtimestamp(s, tz=tz)


def from_epoch_ms(ms: int) -> datetime:
    """From epoch ms."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
