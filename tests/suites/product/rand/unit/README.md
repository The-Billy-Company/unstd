# Randomness API contracts

`test_rand.py` checks secure token shapes, integer/sequence bounds, empty/error cases, and deterministic seeding for scalar and bulk draws. It explicitly forces stdlib generation and exercises the NumPy implementation when available.

Independent `Generator` streams are compared with their stdlib/NumPy reference
engines, including mixed scalar and bulk draws, failed reseeding, backend
selection, invalid draw shapes, and isolation from module/global/crypto state.
A NumPy-free import also checks runtime annotation introspection.

`test_engines.py` compares every explicitly selected NumPy bit generator with
its native reference, including reseeding, failed-seed atomicity, original-item
identity, unchanged PCG64 defaults, backend restrictions, and optional-dependency
errors. Construction checks cover single stdlib seeding and subclass seed hooks.

`test_boundaries.py` covers equal/reversed and arbitrary-precision scalar ranges, empty populations, zero/non-finite/mismatched weights without consuming draws, engine-defined mixed-sign weights, per-backend scalar/bulk engine ownership, NumPy dtypes and int64 limits, and secure-token lengths, alphabets, and argument errors.

`test_bulk_streams.py` checks exact bulk values and full engine states against
stdlib and every supported NumPy generator, including empty/degenerate ranges,
large integer intervals, signed zero, numeric arithmetic hooks, and custom
stdlib methods. Module fallback draws retain their mixed scalar/bulk stream.

`test_keyed_ints.py` pins the explicit SplitMix64-v1 stream, native/Python
agreement, full int64 ranges, rejection sampling, chunk/order independence,
transactional input validation, normal-engine state isolation and GC observers.

`test_native_shuffle.py` qualifies the optional C loop against stdlib output
and full state, then probes callback failures, list mutation, reference
lifetimes, profiling hooks, and pending-signal cancellation.

Seeded sequences are compared within their selected backend. Tests do not require NumPy and stdlib generators to produce identical sequences, and collision samples alone are not treated as proof of cryptographic security.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rand/unit -q
```

See [the module test overview](../README.md) for related evidence.
