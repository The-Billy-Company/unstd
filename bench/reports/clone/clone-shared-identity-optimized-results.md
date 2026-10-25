# Lower identity-memo overhead

The native `clone.deep` path now keeps its local identity map in a pointer-keyed
table. A closed builtin graph no longer allocates a Python integer for every
lookup, builds a Python staging dictionary and source list, or exports entries
into a Python memo that the caller will immediately discard. A small inline
table serves short graphs; larger graphs grow it. Scalar root dictionaries use
a validated shallow-copy shortcut because their values are immutable atoms.

Shared-reference preservation remains part of both modes. When dataclass/model
state must share a memo with Python and foreign copy hooks, the native copier
still imports existing entries and exports completed nodes with source
keep-alives. Partial outputs and failed native attempts remain private.

The comparison uses the exact Python wrapper, native C source and matching
CPython 3.13 extension saved immediately before this optimization. Both
`before-identity` and `optimized-identity` preserve aliases. The `legacy-tree`
control uses the saved extension's earlier tree-copy entrypoint and its prior
public wrapper checks; it intentionally duplicates aliases. Stdlib
`copy.deepcopy` is a fourth control.

## Process CPU results, GC disabled

| Input | Before identity path | Optimized identity path | Speedup |
| --- | ---: | ---: | ---: |
| Three-field scalar dict | 0.272 µs | 0.127 µs | 2.13× |
| Three-item scalar list | 0.278 µs | 0.147 µs | 1.90× |
| 10,000-item scalar list | 39.02 µs | 38.56 µs | 1.01× |
| 200 distinct nested builtin records | 95.70 µs | 50.58 µs | 1.89× |
| One 1,000-item list referenced 500 times | 19.44 µs | 8.16 µs | 2.38× |
| 30-layer shared list graph (31 unique lists) | 5.04 µs | 2.02 µs | 2.50× |
| 200 distinct dataclasses | 236.24 µs | 239.80 µs | 0.99× |

## Process CPU results, GC enabled

| Input | Before identity path | Optimized identity path | Speedup |
| --- | ---: | ---: | ---: |
| Three-field scalar dict | 0.269 µs | 0.126 µs | 2.14× |
| Three-item scalar list | 0.269 µs | 0.150 µs | 1.80× |
| 10,000-item scalar list | 40.02 µs | 38.77 µs | 1.03× |
| 200 distinct nested builtin records | 91.40 µs | 50.55 µs | 1.81× |
| One 1,000-item list referenced 500 times | 19.88 µs | 8.12 µs | 2.45× |
| 30-layer shared list graph (31 unique lists) | 4.69 µs | 1.95 µs | 2.40× |
| 200 distinct dataclasses | 230.68 µs | 236.98 µs | 0.97× |

On the distinct nested builtin tree, the optimized path is within **1%** of the
legacy tree copier in both GC modes. It was previously roughly 1.9× slower in the
[initial identity measurements](clone-shared-identity-results.md). The scalar
dictionary is now within 7–8% of the legacy path in these samples. Small lists
still incur about 50 ns of identity-map overhead. The dataclass sample has a
small slowdown (about 2% without GC; 3% with GC), so this is not a
claim of universal improvement.

The shared 1,000-item-list graph also improves by 2.4–2.5× versus the first
identity-preserving implementation. It is about 390× faster
than the legacy tree copier, which reconstructs all 500 repeated child lists.
The dense shared graph requires only 31 lists to be copied; the legacy tree
entrypoint is excluded because expansion exceeds its bounded tree budget.

## Method

Each mode uses nine alternating-order rounds with prepared inputs, process CPU
and elapsed clocks, adaptive batch sizes, and output destruction included.
Before timing, an independent graph-aware checker verifies exact types,
dictionary order, source isolation, preserved aliases and distinct-node
separation. The legacy control is checked for its expansion semantics. The
GC-enabled run uses the interpreter's ordinary thresholds; the GC-disabled run
matches the main timing harness. Inputs, checks and explicit collection are
outside timed batches.

These are local CPython 3.13 measurements on the development machine, not
performance floors. Background work, allocator layout and interpreter changes
can affect absolute times and small differences. The paired results show the
large distinct-builtin-tree overhead reduction and retained shared-graph
benefit in both GC modes.

[Raw results](clone-shared-identity-optimized-py313.json) retain every CPU/wall
sample, exact source/extension fingerprints, the complete measurement harness,
and the saved pre-optimization Python and C sources under `before_sources`.
Recreate the saved wrapper/native pair from those sources before rerunning the
embedded harness with `--before-dir`, using the same interpreter and build
settings. The source archive's manifest records its original extension hash.
