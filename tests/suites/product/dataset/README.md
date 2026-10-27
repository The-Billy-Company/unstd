# Indexed dataset contracts

`unstd.dataset` turns a JSON row stream into an immutable indexed snapshot.
These tests compare values and selections against independent Python lists,
and verify the artifact with independently constructed binary vectors.

- [unit/](unit/README.md) covers sequence behavior, ordered bulk access, lazy
  slices and batches, integrity checks, corruption, native parity, and ownership.
- [integration/](integration/README.md) covers atomic replacement, failed imports,
  retained reader snapshots, permissions, and compressed NDJSON imports.

```console
uv run --no-sync pytest tests/suites/product/dataset -q
```
