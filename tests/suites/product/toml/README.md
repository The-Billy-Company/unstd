# TOML contracts

Contracts for `unstd.toml`: stdlib-faithful reading and optional comment-preserving writing.

- [unit/](unit/README.md): tomllib behavior, exact style-preserving edit fixtures, date/time/numeric boundaries, TOML 1.0/1.1 dialect fixtures, and missing-extra guards.
- [properties/](properties/README.md): generated read parity, writer round trips, and stateful edit sequences.
- [regression/](regression/README.md): writer output held to TOML 1.0 — ESC spelled `\u001b`, unrepresentable offsets and aware times refused.
- [security/](security/README.md): malformed documents rejected with located errors by both halves.
- [integration/](integration/README.md): real binary/text handles and fixture-owned failing streams.

Reads run without extras. Writer and editable-document cases use the `toml` extra (`tomlkit`); missing-writer behavior is tested explicitly.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml -q
```

See [the module documentation](../../../../src/unstd/toml/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
