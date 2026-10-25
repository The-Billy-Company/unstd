Accelerate ordinary `rand.Generator.shuffle` lists with an optional CPython
kernel that preserves stdlib permutations and engine states. Hoist invariant
work in fallback bulk draws and use NumPy's direct unit-float generator.

Extend `rand.ints` and `Generator.ints` with explicit `seeds=` batches using
stateless SplitMix64-v1. This opt-in changes the seeded stream, preserves normal
RNG states, and has matching native/Python implementations. Captured-consumer
benchmarks retain separate count-phase, full-workflow and compatibility results
on macOS and Linux.
