# Explicit simulation engines and cheaper generator construction

`rand.Generator(..., backend="numpy", bit_generator=...)` accepts `"PCG64"`,
`"PCG64DXSM"`, `"SFC64"`, or `"Philox"`. The selected algorithm survives
reseeding. Existing defaults and seeded scalar/bulk sequences remain unchanged;
other bit generators are explicit new simulation streams, never secure randomness.

Ordinary `Generator` construction initializes the stdlib engine once instead of
seeding from OS entropy before immediately applying the requested seed. Subclasses
retain their initialized state and virtual `seed()` hook. Draw dispatch and instance
slots are unchanged. UUID documentation now distinguishes the fallback's 12-bit
counter from CPython 3.14's 42-bit counter.
