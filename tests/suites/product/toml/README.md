# TOML contracts

Contracts for `unstd.toml`: stdlib-faithful reading and optional comment-preserving writing.

- [unit/](unit/README.md): tomllib behavior, editing helpers, and missing-extra guards.
- [properties/](properties/README.md): generated read parity and optional writer round trips.

Reads run without extras. Writer and editable-document cases use the `toml` extra (`tomlkit`); missing-writer behavior is tested explicitly.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml -q
```

See [the module documentation](../../../../src/unstd/toml/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
