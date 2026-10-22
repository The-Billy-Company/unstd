Fix `timeutil.span` to measure elapsed instants across DST transitions within a
shared IANA zone. Repeated local times and half-hour transitions are covered by
regressions, including reverse durations and string/datetime input agreement.
Offset arithmetic also preserves valid intervals near the datetime year limits
without constructing UTC endpoints that would overflow.
Custom timezones returning no UTC offset follow the floating-input policy:
strict mode rejects them; `assume_utc=True` treats their wall fields as UTC.
