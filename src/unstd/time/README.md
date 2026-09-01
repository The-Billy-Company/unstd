# `unstd.time`

Clock, monotonic, perf-counter, and date arithmetic — the one seam callers route
time reads through, so wall/monotonic/perf timestamps and typed-instant math stay
consistent instead of scattering `datetime.now()` / `whenever` across the tree.

## Modules

| File          | Role                                                                                               |
| ------------- | -------------------------------------------------------------------------------------------------- |
| `timeutil.py` | Wall (`utcnow`, `wall`), monotonic (`mono`), perf (`perf`, `elapsed_ms`), ISO + locale formatting. |
| `dateutil.py` | Typed-instant seam (`whenever`): ISO parsing, DST-correct instant math, ranges, lookback windows.  |

## Backend

`timeutil` is **pure stdlib** and works in a base `unstd` install; its protobuf
`Timestamp` bridge lazy-imports `google.protobuf` on call, so a consumer that
never builds a proto timestamp inherits no protobuf dependency. `dateutil`
hard-requires the `time` extra (`pip install 'unstd[time]'`) — `whenever`'s
DST-correct absolute-instant math has no faithful stdlib substitute.

## DST folds & gaps — `disambiguate=`

`zoned_wall`/`parse_zoned` build a `ZonedDateTime` from *local wall-clock*
fields, which are ambiguous twice a year in any zone that observes DST: a
fall-back **fold** (one wall time occurs twice) and a spring-forward **gap**
(one wall time never occurs). Both take `disambiguate=` (default
`"compatible"`, whenever's RFC 5545-matching rule — a gap shifts forward, a
fold picks the earlier offset):

```python
from unstd.time import dateutil

dateutil.zoned_wall(2023, 10, 29, 1, 15, "Europe/London")  # "compatible" (default)
dateutil.zoned_wall(2023, 10, 29, 1, 15, "Europe/London", disambiguate="earlier")
dateutil.zoned_wall(2023, 10, 29, 1, 15, "Europe/London", disambiguate="later")
dateutil.zoned_wall(
    2023, 10, 29, 1, 15, "Europe/London", disambiguate="raise"
)  # RepeatedTime
```

`disambiguate="raise"` propagates whenever's `RepeatedTime`/`SkippedTime`
(both `ValueError` subclasses) rather than collapsing into the same `None`
that `zoned_wall`/`parse_zoned` return for genuinely invalid fields/zone —
that exception *is* the answer a caller asking for `"raise"` wants. It's a
no-op on `parse_zoned`'s aware branch: an already-aware `raw` carries its own
absolute instant, so there's no fold/gap to resolve.
