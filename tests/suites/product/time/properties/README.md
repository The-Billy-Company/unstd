# Time instant and zone-resolution properties

`test_instants.py` generates aware instants with fixed offsets (including odd-second offsets) and IANA zones. It checks `iso` formatting and truncation, parser round trips, signed `span` differences, exact `from_epoch_ms`/`from_epoch_s` conversion, and epoch reads from a pinned nanosecond clock. Its oracle is integer microseconds since the epoch, computed from wall fields and offsets without `astimezone`.

`test_zoned_laws.py` draws wall times near hand-picked transitions in New York, London, Sydney, Lord Howe, St. John's and Kathmandu, including local-mean-time changes. It compares `compatible`/`earlier`/`later`/`raise` resolution and aware localization against stdlib `zoneinfo` fold semantics.

`test_instants.py` is pure stdlib. `test_zoned_laws.py` requires the `time` extra (`whenever`).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/time/properties -q
```

See [the module test overview](../README.md) for related evidence.
