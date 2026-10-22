# Regular expression contracts

Contracts for `unstd.rex`: Python `re` behavior, native execution where supported, explicit fallback behavior, and retained defects.

- [unit/](unit/README.md): API, flags, cache behavior, and engine selection.
- [parity/](parity/README.md): Unicode, iteration, bounded searches, and replacement callbacks.
- [security/](security/README.md): bounded native execution and fallback trust boundaries.
- [regression/](regression/README.md): surrogate-region behavior and concurrent cache capacity.

The `rex` extra enables irregex/irgx tests. Fallback contracts run without it; native safety claims require observed native execution.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rex -q
```

See [the module documentation](../../../../src/unstd/rex/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
