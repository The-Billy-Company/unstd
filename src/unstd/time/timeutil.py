"""Shared wall-clock, monotonic, and perf timestamp utilities.

The one seam every caller routes clock reads through, so wall/monotonic/perf
timestamps stay consistent instead of scattering ``datetime.now()`` /
``time.monotonic()`` across the tree.

Backend: this module is **pure stdlib** — it works in a base ``unstd``
install with no extra. The only third-party touchpoint is the protobuf
``Timestamp`` bridge (:func:`proto_timestamp` / :func:`proto_timestamp_ms`),
which lazily imports ``google.protobuf`` on call and lives behind the ``proto``
extra, so a consumer that never builds a proto timestamp inherits zero protobuf
dependency and a consumer that does gets the same actionable ImportError every
other guarded backend in this package raises.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from time import monotonic, monotonic_ns, perf_counter, time
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo


if TYPE_CHECKING:
    from google.protobuf.timestamp_pb2 import Timestamp


_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DATE_FMT = "%Y-%m-%d"
_PROTO_HINT = (
    "unstd.time.timeutil's protobuf Timestamp bridge requires the 'proto' extra "
    "— install with: pip install 'unstd[proto]'"
)


def _human(dt: datetime) -> str:
    """Render *dt* in the house long form: ``Monday, August 3, 2026 at 9:05 PM PDT``.

    The unpadded day and hour are substituted here rather than spelled ``%-d`` /
    ``%-I``, which are a glibc/BSD extension: ``strftime`` raises ``ValueError``
    on Windows for both, so the old format string made every human-facing
    timestamp in this module a hard failure on one of the three platforms the
    package claims. Only portable codes reach ``strftime`` now.
    """
    return dt.strftime(f"%A, %B {dt.day}, %Y at {dt.hour % 12 or 12}:%M %p %Z")


def _to_proto() -> type[Timestamp]:
    """Import ``google.protobuf``'s ``Timestamp``, or raise naming the extra."""
    try:
        from google.protobuf.timestamp_pb2 import Timestamp
    except ImportError as exc:
        raise ImportError(_PROTO_HINT) from exc
    # protobuf's generated modules carry no stubs, so the imported symbol lands
    # as `Any` and the declared return type would be silently unenforced.
    return cast("type[Timestamp]", Timestamp)


def utcnow() -> datetime:
    """The current instant as a UTC-aware datetime (never naive)."""
    return datetime.now(UTC)


def now_tz(tz: tzinfo | None = None) -> datetime:
    """Return the current aware datetime in *tz* (UTC when omitted)."""
    return datetime.now(tz or UTC)


def utcnow_iso() -> str:
    """The current instant as a second-granular RFC-3339 UTC stamp (``…T12:00:00Z``)."""
    return datetime.now(UTC).strftime(_ISO_FMT)


def utcnow_iso_ms() -> str:
    """RFC-3339 UTC stamp with millisecond precision (``…T12:00:00.123Z``).

    The telemetry-grade twin of :func:`utcnow_iso`: log/trace ordering needs
    sub-second resolution (whole-second stamps make concurrent hops sort
    arbitrarily), while everything else keeps the shorter second-granular form.
    """
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def today() -> date:
    """Return today's date in the local timezone."""
    return datetime.now(UTC).astimezone().date()


def today_iso() -> str:
    """Return today's local date as ``YYYY-MM-DD``."""
    return datetime.now(UTC).astimezone().date().isoformat()


def utcnow_date_iso() -> str:
    """Today's date **in UTC** as ``YYYY-MM-DD`` — the UTC twin of :func:`today_iso`.

    These two disagree by a day for roughly half of every day at either end of
    the world; pick by whether the caller's day boundary is the user's or the
    fleet's.
    """
    return datetime.now(UTC).strftime(_DATE_FMT)


def wall() -> float:
    """Seconds since the Unix epoch as a float — wall clock, so it can jump."""
    return time()


def epoch_s() -> int:
    """Whole seconds since the Unix epoch, truncated (not rounded)."""
    return int(time())


def epoch_ms() -> int:
    """Whole milliseconds since the Unix epoch, truncated (not rounded)."""
    return int(time() * 1000)


def mono() -> float:
    """A monotonic clock read in seconds — never goes backwards, no fixed epoch.

    Only differences between two reads mean anything; the absolute value does not.
    Use this, never :func:`wall`, to measure a duration that must survive an NTP
    step or a DST change.
    """
    return monotonic()


def mono_ns() -> int:
    """:func:`mono` in integer nanoseconds — no float rounding at long uptimes."""
    return monotonic_ns()


def perf() -> float:
    """The highest-resolution clock available, for timing a short span."""
    return perf_counter()


def elapsed_ms(start: float) -> float:
    """Milliseconds elapsed since *start*, which must have come from :func:`perf`."""
    return (perf_counter() - start) * 1000


def human_now() -> str:
    """The current local time in long form (``Monday, August 3, 2026 at 9:05 PM PDT``)."""
    return _human(datetime.now().astimezone())


def human_now_tz(tz_name: str = "") -> str:
    """Format current time in the given IANA timezone (e.g. 'America/New_York').

    An empty *tz_name* means the local zone, matching :func:`human_now`.
    """
    # The zone has to survive to strftime. The previous spelling read the clock
    # in `tz_name` and then chained a bare `.astimezone()`, which converts to the
    # LOCAL zone — so every zone rendered as the host's own and the argument did
    # nothing at all.
    return _human(
        datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now().astimezone()
    )


def iso_fmt(dt: datetime) -> str:
    """Render *dt* as a second-granular RFC-3339 UTC stamp (``2026-01-15T14:30:00Z``).

    The trailing ``Z`` is an assertion that the instant is UTC, so an aware *dt*
    in another zone is **converted** rather than relabelled — formatting a
    ``America/New_York`` wall time and pinning a ``Z`` to it silently moved the
    instant by the offset. A naive *dt* is assumed to already be UTC, the same
    convention :func:`proto_timestamp` documents.
    """
    utc = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return utc.strftime(_ISO_FMT)


def date_fmt(dt: datetime) -> str:
    """Render *dt*'s calendar date as ``YYYY-MM-DD``, in whatever zone it carries."""
    return dt.strftime(_DATE_FMT)


def proto_timestamp(value: str | datetime | None) -> Timestamp | None:
    """Build a ``google.protobuf.Timestamp`` from an ISO-8601 / RFC3339 string or ``datetime``; ``''`` / ``None`` / unparseable → ``None`` (the not-set sentinel, mirroring the pre-migration empty-string-means-unset contract and the Go ``timex.FromRFC3339`` seam). A date-only ``YYYY-MM-DD`` is read as UTC midnight; naive datetimes are assumed UTC. Pass the result straight to a message field (constructor kwarg accepts ``None`` for unset) or ``CopyFrom`` it onto one.

    Lazily imports ``google.protobuf`` (the ``proto`` extra) — the timeutil base
    surface stays protobuf-free for consumers that never build a proto timestamp.
    Raises ``ImportError`` naming the extra when it is not installed.
    """
    timestamp = _to_proto()

    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = f"{s[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ts = timestamp()
    ts.FromDatetime(dt.astimezone(UTC))
    return ts


def proto_timestamp_ms(ms: int) -> Timestamp:
    """Build a ``google.protobuf.Timestamp`` from epoch milliseconds.

    Behind the ``proto`` extra, like :func:`proto_timestamp`.
    """
    ts = _to_proto()()
    ts.FromMilliseconds(ms)
    return ts
