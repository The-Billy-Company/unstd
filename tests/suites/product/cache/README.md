# Persistent cache contracts

Independent contracts for `unstd.cache.Cache`, covering persistent JSON values,
expiry, namespace isolation, bounded storage, and transactional cache fills.

- [unit/](unit/README.md): public value/configuration behavior and deterministic
  expiry/eviction histories.
- [integration/](integration/README.md): separate interpreters, simultaneous
  cache fills, SQLite writer contention, and producer interruption.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/cache -q
```

These tests require no optional product dependencies.
