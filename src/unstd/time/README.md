---
doc_radar:
  sentinels:
    - file: src/unstd/time/timeutil.py
      contains: ['def span(', 'def proto_timestamp(']
    - file: src/unstd/time/zoned.py
      contains: ['def wall(']
---

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

## Source ownership

This flat leaf separates absolute instants from local wall-clock disambiguation;
both concerns retain their existing public module names.

| File          | Role                                                                                                                         |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| [`__init__.py`](__init__.py) | Package documentation; callers import the time utility and zone modules independently. |
| [`timeutil.py`](timeutil.py) | Stdlib clock bindings, epoch conversion, formatting, parsing, elapsed spans, and the lazy protobuf bridge. |
| [`zoned.py`](zoned.py) | Optional `whenever` backend, DST-correct wall time, zone parsing, and UTC conversion. |

Run the [time contracts](../../../tests/suites/product/time/README.md) from
the repository root with `uv run --no-sync pytest tests/suites/product/time -q`.

Absolute instants — parsing, formatting and elapsed durations — live in
`timeutil` and cost no dependency. Durations account for each endpoint's UTC
offset, so DST changes within a shared IANA zone still measure elapsed time.
Offsets are applied to the difference without constructing UTC endpoints,
preserving valid intervals near the datetime year limits. Local wall-clock
construction and disambiguation live in `zoned`, behind the `time` extra.

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
`wall`/`parse` return for invalid date/time fields. `wall` also returns `None`
for an unknown zone, while `parse` propagates `TimeZoneNotFoundError`. It's a no-op on `parse`'s aware
branch: an already-aware `raw` carries its own absolute instant, so there's no
fold or gap to resolve.
