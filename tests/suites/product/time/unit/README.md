# Time utility and zoned contracts

`test_timeutil.py` covers clock relationships, UTC conversion/formatting, portable human-readable dates, parsing, epoch helpers, elapsed spans, DST/offset edges, and the optional protobuf bridge. `test_zoned.py` checks instant and wall-time construction, including Europe/London folds and gaps with explicit disambiguation.

Zoned tests require the `time` extra (`whenever`); protobuf cases use the separate `proto` extra. Utility tests otherwise use the stdlib and compare clocks through bounds rather than fixed current times.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/time/unit -q
```

See [the module test overview](../README.md) for related evidence.
