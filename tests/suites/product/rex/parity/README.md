# Regex differential contracts

`test_fidelity.py` compares results with stdlib `re`, including Unicode case behavior, iteration, bounded regions, captures, replacement callbacks, and fallback boundaries. Native-specific cases check that compatible patterns actually use the native engine.

Common behavior runs without extras. Native cases skip when irregex/irgx is unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rex/parity -q
```

See [the module test overview](../README.md) for related evidence.
