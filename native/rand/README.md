# Seed-faithful random kernels

`src/adapter.c` implements the optional `unstd.rand._rand_native` extension
using the public CPython C API on Python 3.12–3.15. It contains no platform
instructions, OS interfaces, or dependency on CPython's
private `Random` object layout.

`shuffle(getrandbits, items)` runs Fisher–Yates over an exact Python list.
Every index uses the same bit length and rejection sampling as stdlib
`Random._randbelow_with_getrandbits`, calling the live bound `getrandbits`
method. The Python facade admits supported instance shuffles and retains
stdlib behavior for other sequence or engine shapes. Module-level `shuffle`
remains the exact stdlib alias. The selected engine's seeded output and complete
state consumption stay equal to stdlib, including rejection draws.

The kernel holds the GIL and owns no retained state or buffers. Callback,
integer-conversion and callback-result finalizer exceptions propagate. List
size is checked after each callback and result cleanup; a size change raises
`RuntimeError` before any native swap. Item references are acquired only after
all callbacks and both are owned during checked list assignments. Periodic
signal checkpoints cover both ordinary draws and rejection loops.
`KERNEL_VERSION` identifies the implementation in performance receipts.

The unit qualification compares empty, small and large seeded shuffles and
full engine states against stdlib. It also probes repeated calls, rejection
sampling, callback failures, list growth/shrinkage, invalid integer results, result-finalizer
mutation, reference lifetimes, profiling callbacks and cancellation. The kernel
is an optimization of simulation shuffling; secure draws remain in `secrets`.

`src/keyed.h` implements the explicit stateless `ints(..., seeds=...)` path.
It uses the public-domain [SplitMix64 reference](https://prng.di.unimi.it/splitmix.c)
algorithm with local uint64 state per key and modulo rejection. No normal RNG
state is read or advanced. All seeds are snapshotted and validated before output
generation; the temporary integer buffer is freed on every exit. The output
list stays untracked by GC until all slots contain valid integers, so signal
handlers can safely inspect tracked objects. Signed output conversion avoids
implementation-defined unsigned casts, including the full int64 range.

The Python fallback in `src/unstd/rand/keyed.py` implements the same versioned
stream. Tests pin reference words, rejection, integer boundaries, reordering,
chunking, validation hooks, both output containers, ordinary-state isolation
and signal-handler GC inspection. This opt-in changes values relative to MT19937;
it remains a simulation PRNG.
