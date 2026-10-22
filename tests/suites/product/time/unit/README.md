# Time utility and zoned contracts

`test_timeutil.py` covers clock relationships, UTC conversion/formatting, portable human-readable dates, parsing, epoch helpers, elapsed spans, DST/offset edges, and the optional protobuf bridge. `test_zoned.py` checks instant and wall-time construction, including Europe/London folds and gaps with explicit disambiguation.

`test_pinned_bounds.py` pins the clock and host zone to check exact `human`/`iso`/`today`/`now_tz`/`span` output in several zones, epoch reads, year-1/9999 bounds, nanosecond truncation, historical offsets, and the protobuf bridge against `Timestamp`'s declared range, both installed and absent. `test_zone_transitions.py` resolves hand-written transition instants for New York, London, Sydney, Lord Howe, St. John's, Kathmandu and local-mean-time offsets. It checks the fixture table against `zoneinfo` first.

Zoned and transition tests require the `time` extra (`whenever`); protobuf cases use the separate `proto` extra and fail rather than skip under `--require-extras`. Utility tests otherwise use the stdlib.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/time/unit -q
```

See [the module test overview](../README.md) for related evidence.
