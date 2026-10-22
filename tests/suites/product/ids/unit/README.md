# UUID API contracts

`test_uid.py` pins UUIDv7 version/variant, uniqueness, formatting, and ordering, plus stable UUIDv5 derivation vectors and namespace behavior. Derivation expectations are fixed independently of the implementation because changing them would rename persisted identifiers.

The public generator uses the optional UUID backend when available and its Python fallback otherwise. No extra is required to run this leaf.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/ids/unit -q
```

See [the module test overview](../README.md) for related evidence.
