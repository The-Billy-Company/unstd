# Time contracts

Contracts for `unstd.time.timeutil` and `unstd.time.zoned`: clock helpers, UTC formatting/parsing, elapsed spans, and DST disambiguation.

- [unit/](unit/README.md): portable time utilities, pinned clocks, calendar bounds, optional protobuf bridging, zoned wall time, and hand-authored IANA transition families.
- [properties/](properties/README.md): generated instant laws and zone resolution checked against `zoneinfo`.
- [regression/](regression/README.md): exact `from_epoch_ms` milliseconds at the calendar limits.

The utility surface is stdlib-based. Zoned tests require the `time` extra (`whenever`), and protobuf bridge cases require the separate `proto` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/time -q
```

See [the module documentation](../../../../src/unstd/time/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
