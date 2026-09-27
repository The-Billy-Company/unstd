# `unstd.time`

Clocks and time — the one seam callers route time reads through, so wall /
monotonic / perf timestamps, RFC-3339 stamps, and instant math stay consistent
instead of scattering `datetime.now()` / `whenever` across the tree.

```python
from unstd.time import timeutil, zoned

t0 = timeutil.perf()
timeutil.elapsed_ms(t0)
timeutil.iso()  # "2026-01-15T14:30:00Z" — now
timeutil.iso(dt, ms=True)  # "2026-01-15T14:30:00.123Z"
timeutil.parse_utc(raw)  # aware-UTC datetime, or None
timeutil.span(created_at, unit="days", assume_utc=True)  # age, or None
zoned.wall(2026, 3, 8, 2, 30, "America/New_York")  # DST gap → 3:30 EDT
```

## Modules

| File          | Role                                                                                                                         |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `timeutil.py` | Clocks (`wall`, `mono`, `perf`, `elapsed_ms`, `epoch_*`), `iso` / `human` formatting, ISO parsing, and `span`. Pure stdlib. |
| `zoned.py`    | DST-correct wall-clock time in an IANA zone (`wall`, `parse`, `now`, `instant`, `to_utc`) over `whenever`.                 |

The split is by what stdlib gets right. Absolute instants — parse, format,
subtract two aware datetimes — stdlib does correctly, so all of it is in
`timeutil` and costs no dependency. Wall-clock time in a zone it gets wrong
silently, so that is `zoned`, behind the `time` extra.

## Backend

`timeutil` is **pure stdlib** and works in a base `unstd` install. The clock
reads *are* the stdlib functions, bound directly — no wrapper frame on the
hottest calls in a program. Its protobuf `Timestamp` bridge lazy-imports
`google.protobuf` once, behind the `proto` extra. `zoned` hard-requires the
`time` extra (`pip install 'unstd[time]'`).

## Strict by default — `span` and `assume_utc`

`span(start, end=None, *, unit)` is the one signed duration: `end - start` in
seconds, minutes, hours, or days, with `end` defaulting to now. A floating
value (no offset, or date-only) names no instant, so by default it yields `None`
rather than being quietly placed in UTC. `assume_utc=True` is the lenient
reading for legacy tz-less stamps — an event's own offset cancels in the
subtraction, so the duration between two floating values is still exact.

## DST folds & gaps — `disambiguate=`

`zoned.wall` / `zoned.parse` build a `ZonedDateTime` from *local wall-clock*
fields, which are ambiguous twice a year in any zone that observes DST: a
fall-back **fold** (one wall time occurs twice) and a spring-forward **gap**
(one wall time never occurs). Both take `disambiguate=` (default
`"compatible"`, whenever's RFC 5545-matching rule — a gap shifts forward, a
fold picks the earlier offset):

```python
zoned.wall(2023, 10, 29, 1, 15, "Europe/London")  # "compatible" (default)
zoned.wall(2023, 10, 29, 1, 15, "Europe/London", disambiguate="earlier")
zoned.wall(2023, 10, 29, 1, 15, "Europe/London", disambiguate="later")
zoned.wall(2023, 10, 29, 1, 15, "Europe/London", disambiguate="raise")  # RepeatedTime
```

`disambiguate="raise"` propagates whenever's `RepeatedTime`/`SkippedTime`
(both `ValueError` subclasses) rather than collapsing into the same `None`
`wall`/`parse` return for genuinely invalid fields or zone — that exception *is*
the answer a caller asking for `"raise"` wants. It's a no-op on `parse`'s aware
branch: an already-aware `raw` carries its own absolute instant, so there's no
fold or gap to resolve.
