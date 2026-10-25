"""Clocks, RFC-3339 formatting, ISO parsing, and instant arithmetic — pure stdlib.

The one seam every caller routes clock reads through, so wall/monotonic/perf
timestamps stay consistent instead of scattering ``datetime.now()`` /
``time.monotonic()`` across the tree::

    from unstd.time import timeutil

    t0 = timeutil.perf()  # …work…
    timeutil.elapsed_ms(t0)
    timeutil.iso()  # "2026-01-15T14:30:00Z" — now, UTC
    timeutil.iso(dt, ms=True)  # "2026-01-15T14:30:00.123Z"
    timeutil.parse_utc(raw)  # aware-UTC datetime, or None
    timeutil.span(start_raw, end_raw, unit="minutes")  # float, or None

The clock reads (:data:`wall`, :data:`mono`, :data:`mono_ns`, :data:`perf`) *are*
the stdlib functions, bound directly: no wrapper frame on the hottest calls in a
program, and patching the module attribute in a test still redirects every
caller that reads it through the module.

Everything here is absolute-instant work. Durations account for each endpoint's
UTC offset so a shared IANA zone cannot select wall-clock arithmetic.
DST-correct *wall-clock* work — "9am in New York next Tuesday" — is
:mod:`unstd.time.zoned`, behind the ``time`` extra.

Backend: **pure stdlib** — works in a base ``unstd`` install with no extra. The
only third-party touchpoint is the protobuf ``Timestamp`` bridge
(:func:`proto_timestamp` / :func:`proto_timestamp_ms`), which lazily imports
``google.protobuf`` on first call and lives behind the ``proto`` extra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as _civil_time
from functools import cache, partial
import time as _time
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo


if TYPE_CHECKING:
    from google.protobuf.timestamp_pb2 import Timestamp


__all__ = [
    "Unit",
    "date_from_iso",
    "date_today",
    "datetime_today",
    "elapsed_ms",
    "epoch_ms",
    "epoch_s",
    "from_epoch_ms",
    "from_epoch_s",
    "from_iso",
    "from_timestamp",
    "human",
    "iso",
    "mono",
    "mono_ns",
    "now",
    "now_tz",
    "parse_date",
    "parse_iso",
    "parse_utc",
    "perf",
    "perf_ns",
    "process_time",
    "process_time_ns",
    "proto_timestamp",
    "proto_timestamp_ms",
    "sleep",
    "span",
    "thread_time",
    "thread_time_ns",
    "time_from_iso",
    "today",
    "utcnow",
    "wall",
    "wall_ns",
]

type Unit = Literal["seconds", "minutes", "hours", "days"]
_SECONDS: dict[str, int] = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

# The unpadded day and hour are substituted in Python rather than spelled ``%-d`` /
# ``%-I``, a glibc/BSD extension ``strftime`` rejects on Windows.
_HUMAN = "%A, %B {day}, %Y at {hour}:%M %p %Z"
_PROTO_HINT = (
    "unstd.time.timeutil's protobuf Timestamp bridge requires the 'proto' extra "
    "— install with: pip install 'unstd[proto]'"
)


# ── clocks ─────────────────────────────────────────────────────────────────────

wall = _time.time
"""Seconds since the Unix epoch as a float — wall clock, so it can jump."""

wall_ns = _time.time_ns
"""The wall clock in integer nanoseconds — no float rounding of the epoch."""

mono = _time.monotonic
"""A monotonic clock read in seconds — never goes backwards, no fixed epoch.

Only differences between two reads mean anything. Use this, never :data:`wall`,
to measure a duration that must survive an NTP step or a DST change.
"""

mono_ns = _time.monotonic_ns
""":data:`mono` in integer nanoseconds — no float rounding at long uptimes."""

perf = _time.perf_counter
"""The highest-resolution clock available, for timing a short span."""

perf_ns = _time.perf_counter_ns
""":data:`perf` in integer nanoseconds, without a conversion through float."""

process_time = _time.process_time
"""CPU seconds consumed by this process, excluding time spent asleep."""

process_time_ns = _time.process_time_ns
""":data:`process_time` in integer nanoseconds."""

if hasattr(_time, "thread_time"):
    thread_time = _time.thread_time
    """CPU seconds consumed by the calling thread, where the host supports it."""

    thread_time_ns = _time.thread_time_ns
    """:data:`thread_time` in integer nanoseconds."""

else:
    __all__ = [
        name for name in __all__ if name not in ("thread_time", "thread_time_ns")
    ]

sleep = _time.sleep
"""Suspend the calling thread, retaining the platform's sleep and signal semantics."""


def elapsed_ms(start: float) -> float:
    """Milliseconds elapsed since *start*, which must have come from :data:`perf`."""
    return (_time.perf_counter() - start) * 1000


def epoch_s() -> int:
    """Whole seconds since the Unix epoch, truncated (not rounded)."""
    return _time.time_ns() // 1_000_000_000


def epoch_ms() -> int:
    """Whole milliseconds since the Unix epoch — exact integer math, no float rounding."""
    return _time.time_ns() // 1_000_000


utcnow = partial(datetime.now, UTC)
"""The current instant as a UTC-aware datetime (never naive) — ``datetime.now(UTC)``, pre-bound."""

now = datetime.now
"""The stdlib clock unchanged: local and naive by default, or aware with an explicit zone."""

date_today = date.today
"""The native local calendar date, retaining the stdlib read path without a wrapper."""

datetime_today = datetime.today
"""The native local-naive datetime, retaining the stdlib's ``today`` read path."""


def now_tz(tz: tzinfo) -> datetime:
    """The current aware datetime in *tz*. UTC now is :data:`utcnow`."""
    return datetime.now(tz)


def today(*, utc: bool = False) -> date:
    """Today's date — in the local zone, or in UTC with ``utc=True``.

    The two disagree by a day for roughly half of every day at either end of the
    world; pick by whether the caller's day boundary is the user's or the fleet's.
    """
    return datetime.now(UTC if utc else None).date()


# ── formatting ─────────────────────────────────────────────────────────────────


def iso(dt: datetime | None = None, *, ms: bool = False) -> str:
    """*dt* (default: now) as an RFC-3339 UTC stamp — ``…T14:30:00Z``, or ``…T14:30:00.123Z`` with *ms*.

    The trailing ``Z`` is an assertion that the instant is UTC, so an aware *dt*
    in another zone is **converted** rather than relabelled. A naive *dt* is
    assumed to already be UTC, the same convention :func:`proto_timestamp` uses.
    Sub-second digits are truncated, never rounded, so a stamp never lands in the
    future of the instant it names. ``ms=True`` is the telemetry-grade form: log
    and trace ordering needs sub-second resolution.
    """
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    spec = "milliseconds" if ms else "seconds"
    return f"{dt.replace(tzinfo=None).isoformat('T', spec)}Z"


def human(tz: str = "") -> str:
    """The current time in long form — ``Monday, August 3, 2026 at 9:05 PM PDT``.

    *tz* is an IANA zone name (``"America/New_York"``); empty means the local zone.
    """
    return _human(datetime.now(ZoneInfo(tz)) if tz else datetime.now().astimezone())


def _human(dt: datetime) -> str:
    return dt.strftime(_HUMAN.format(day=dt.day, hour=dt.hour % 12 or 12))


# ── parsing ────────────────────────────────────────────────────────────────────

from_iso = datetime.fromisoformat
"""Decode an ISO datetime unchanged; invalid input raises the stdlib exception."""

date_from_iso = date.fromisoformat
"""Decode an ISO calendar date, retaining the stdlib's accepted forms and errors."""

time_from_iso = _civil_time.fromisoformat
"""Decode an ISO time, retaining offsets and the stdlib's errors without coercion."""

from_timestamp = datetime.fromtimestamp
"""Decode epoch seconds with the native local-naive default and optional timezone."""


def parse_iso(raw: str) -> datetime | None:
    """ISO-8601 / RFC-3339 *raw* as written — aware if it carries an offset, naive if not; ``None`` when unparseable.

    ``Z`` and a date-only ``YYYY-MM-DD`` (read as midnight) both parse, since
    ``datetime.fromisoformat`` accepts them from Python 3.11.
    """
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def parse_utc(raw: str) -> datetime | None:
    """*raw* as an aware-UTC ``datetime`` — a naive value read as UTC — or ``None`` when unparseable.

    The lenient reading: use it where a legacy tz-less stamp should still anchor
    somewhere rather than crash an aware/naive compare.
    """
    if (dt := parse_iso(raw)) is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_date(raw: str) -> date | None:
    """A ``YYYY-MM-DD`` calendar date, or ``None`` when *raw* is not exactly one."""
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def from_epoch_s(s: float, tz: tzinfo = UTC) -> datetime:
    """The aware datetime *s* seconds after the Unix epoch, in *tz*."""
    return datetime.fromtimestamp(s, tz=tz)


def from_epoch_ms(ms: int, tz: tzinfo = UTC) -> datetime:
    """The aware datetime *ms* milliseconds after the Unix epoch, in *tz*."""
    # Float division loses millisecond alignment at far-calendar timestamps.
    s, rem = divmod(ms, 1000)
    result = datetime.fromtimestamp(s, UTC)
    if type(ms) is int:
        # Integer divmod guarantees 0 <= rem < 1000; setting the UTC field
        # avoids allocating and adding a timedelta for the fractional second.
        if rem:
            result = result.replace(microsecond=rem * 1000)
    else:
        # Keep timedelta's numeric coercion and rounding for other input types.
        result += timedelta(milliseconds=rem)
    return result if tz is UTC else result.astimezone(tz)


# ── instant arithmetic ─────────────────────────────────────────────────────────


def _endpoint(value: str | datetime | None) -> datetime | None:
    if value is None:
        return datetime.now(UTC)
    return value if isinstance(value, datetime) else parse_iso(value)


def _instant(dt: datetime, assume_utc: bool) -> tuple[datetime, timedelta] | None:
    if (offset := dt.utcoffset()) is None and not assume_utc:
        return None
    return dt.replace(tzinfo=None), offset or timedelta()


def span(
    start: str | datetime,
    end: str | datetime | None = None,
    *,
    unit: Unit,
    assume_utc: bool = False,
) -> float | None:
    """Signed ``end - start`` in *unit*, where *end* defaults to now — or ``None`` when either side has no instant.

    Each side is an ISO/RFC-3339 string or a ``datetime``. Strict by default: a
    floating (offset-less or date-only) value names no instant, so it yields
    ``None`` rather than being silently placed in UTC. ``assume_utc=True`` is the
    lenient reading for legacy tz-less stamps — an event's own offset cancels in
    the subtraction, so a duration between two floating values is still exact.
    Negative when *end* precedes *start*; clamp if you only want elapsed time.
    Each endpoint's UTC offset is accounted for, including two local datetimes
    in the same zone on opposite sides of a DST transition.

        span(created_at, unit="days", assume_utc=True)  # age
        span(now, deadline, unit="minutes")  # time remaining
    """
    a, b = _endpoint(start), _endpoint(end)
    if a is None or b is None:
        return None
    if (
        type(a) is datetime
        and type(b) is datetime
        and a.tzinfo is UTC
        and b.tzinfo is UTC
    ):
        return (b - a).total_seconds() / _SECONDS[unit]
    first, last = _instant(a, assume_utc), _instant(b, assume_utc)
    if first is None or last is None:
        return None
    # Subtract wall fields and offsets separately: direct aware subtraction
    # ignores offsets for a shared tzinfo, while constructing UTC endpoints can
    # overflow at datetime.min/max even when the duration is representable.
    return (last[0] - first[0] - last[1] + first[1]).total_seconds() / _SECONDS[unit]


# ── protobuf Timestamp bridge (the `proto` extra) ──────────────────────────────


@cache
def _timestamp() -> type[Timestamp]:
    """Import ``google.protobuf``'s ``Timestamp`` once, or raise naming the extra."""
    try:
        from google.protobuf.timestamp_pb2 import Timestamp
    except ImportError as exc:
        raise ImportError(_PROTO_HINT) from exc
    return Timestamp


def proto_timestamp(value: str | datetime | None) -> Timestamp | None:
    """A ``google.protobuf.Timestamp`` from an ISO-8601 / RFC-3339 string or ``datetime``; ``''`` / ``None`` / unparseable → ``None``.

    ``None`` is the not-set sentinel (the Go ``timex.FromRFC3339`` seam's
    contract), so the result drops straight into a message constructor. A naive
    value — datetime or string, date-only included — is read as UTC, exactly as
    :func:`parse_utc` reads it. Raises ``ImportError`` naming the ``proto`` extra
    when protobuf is not installed.
    """
    timestamp = _timestamp()
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        parsed = parse_utc(value.strip())
        if parsed is None:
            return None
        dt = parsed
    (ts := timestamp()).FromDatetime(dt)
    return ts


def proto_timestamp_ms(ms: int) -> Timestamp:
    """A ``google.protobuf.Timestamp`` from epoch milliseconds (the ``proto`` extra)."""
    (ts := _timestamp()()).FromMilliseconds(ms)
    return ts
