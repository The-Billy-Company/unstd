# Randomness API contracts

`test_rand.py` checks secure token shapes, integer/sequence bounds, empty/error cases, and deterministic seeding for scalar and bulk draws. It explicitly forces stdlib generation and exercises the NumPy implementation when available.

Independent `Generator` streams are compared with their stdlib/NumPy reference
engines, including mixed scalar and bulk draws, failed reseeding, backend
selection, invalid draw shapes, and isolation from module/global/crypto state.
A NumPy-free import also checks runtime annotation introspection.

Seeded sequences are compared within their selected backend. Tests do not require NumPy and stdlib generators to produce identical sequences, and collision samples alone are not treated as proof of cryptographic security.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rand/unit -q
```

See [the module test overview](../README.md) for related evidence.
