# Clone and UUID performance audit

All **15 implemented Python functions** were exercised, together with the four
direct stdlib aliases. The audit preserves public signatures, return types,
backend selection, secure entropy, counter synchronization, and custom copy
protocols. It removes measured redundant work; finite profiling cannot prove
global optimality for every graph, interpreter, backend version, and machine.

Measurements compare Git revision
`e0a447d4593ae698623bfb7660c944462d3d3aa1` with the recorded source hashes in
[`clone-ids-results.json`](clone-ids-results.json). They were collected on
2026-10-18, macOS arm64, CPython 3.12.13, uuid-utils 1.0.0, Pydantic 2.13.5,
and msgspec 0.22.0. Optional dependencies and public APIs are unchanged.

## Method

The driver loads original/current modules into separate namespaces and validates
54 workloads before timing. Checks observe nested types, dictionary key order,
default factories, mutable reference isolation, model state, UUID version and
variant, and alias identity. UUID derivation uses the literal published
`python.org` v5 answer. Profile-only runs exercise cold class admission and all
four handwritten UUID counter branches; ordinary minting uses real entropy and
wall clocks.

Each timed workload uses nine calibrated batches, repeated three times with
before/after order alternating. The recorded result is the median of the three
minimum-batch costs. GC is disabled during batches and restored afterward.
Profiler instrumentation is absent from these timings. Raw cProfile entries are
aggregated explicitly across the two UUID namespaces, avoiding `pstats`' duplicate
source-location overwrite. All raw samples, spreads, function call counts,
self/cumulative profile times, public signatures, and source hashes are retained
in the JSON report.

The final run recorded the following median costs. A ratio above 1 means the
current version was faster; **noisy** means at least one underlying batch series
had a spread above 25%.

| Workload | Before | After | Before/after | Noise |
|---|---:|---:|---:|---|
| Clone model, 100 private attributes | 20.26 µs | 17.15 µs | 1.18× | Stable |
| Clone 40 models | 54.75 µs | 50.80 µs | 1.08× | Noisy |
| `asdict`, small record | 407.8 ns | 384.0 ns | 1.06× | Noisy |
| `asdict`, 200 nested records | 59.95 µs | 62.10 µs | 0.97× | Noisy |
| UUID fallback, canonical text | 2.58 µs | 2.39 µs | 1.08× | Noisy |
| UUID fallback, bare hex | 2.33 µs | 2.18 µs | 1.07× | Noisy |
| UUID fallback, UUID object | 2.06 µs | 1.96 µs | 1.05× | Noisy |
| Native UUID, canonical text | 161.2 ns | 160.1 ns | 1.01× | Stable; unchanged code |
| Native UUID, bare hex | 153.4 ns | 150.8 ns | 1.02× | Noisy; unchanged code |
| UUID derivation | 1.185 µs | 1.183 µs | 1.00× | Noisy; same function object |

The private-attribute improvement is the strongest timing evidence. Forty-nine
of 54 workloads had at least one noisy series, so small differences elsewhere
are not treated as proof of gains or regressions. The changes remove identifiable
work, but the retained measurements do not establish an end-to-end improvement
for every input. All 54 results, including losses and unchanged-code controls,
are saved rather than selecting only faster measurements.

Source snapshots can be reconstructed and compared with:

```console
mkdir -p /tmp/clone-ids-before
git archive e0a447d4593ae698623bfb7660c944462d3d3aa1 src/unstd/clone src/unstd/ids | tar -x -C /tmp/clone-ids-before
uv run --no-sync python -m bench.clone_ids_profile --compare-source /tmp/clone-ids-before/src --rounds 9 --repetitions 3 --output /tmp/clone-ids.json
```

## Decisions by function

| Function | Decision |
|---|---|
| `clone.deep` | Keep the atomic return and whole-graph recursion fallback. |
| `_walk` | Keep inline exact-type leaf checks and container comprehensions; this dominates clone CPU work. |
| `_default_protocol` | Keep copy-protocol validation, cached once per class. |
| `_admit` | Preserve custom dataclass pickle arguments and msgspec reducers/copyreg registration; these paths now delegate to stdlib. |
| `_same` | Already a single return for enum singletons. |
| `_dataclass` | Keep original reconstruction; direct state transfer lacked a consistent gain and changed exotic state semantics. |
| `_reduced` | Keep the reducer invocation and walked arguments; general reducer shapes still need stdlib reconstruction. |
| `_model` | Replace a Python filter traversal plus recursive dictionary traversal with a C dictionary snapshot and one combined filter/clone traversal. |
| `_plain` | Keep cached field names and inline atomic leaf handling. |
| `clone.asdict` | Inline the exact stdlib dataclass predicate to avoid a helper call, preserving metaclass access/errors, custom factories, non-instance errors, and special-container fallback. |
| `uid._seed_counter` | Bind `int.from_bytes` once and use its big-endian default. Keep a fresh two-byte secure entropy request. |
| `uid._next_ms_and_counter` | Remove the second backward-time comparison after the ordering branch has established the timestamp bound. Keep the lock and reseeding rules. |
| `uid._stdlib_uuid7` | Combine constant version/variant flags at compile time and reuse the bound conversion. Keep portable `UUID(int=...)` construction. |
| `uid.new` | Already one backend mint and the required canonical string conversion. |
| `uid.new_hex` | Already one backend mint and the required hex extraction. |
| `clone.copy`, `clone.deepcopy`, `uid.parse`, `uid.derive` | Retain their exact stdlib identities; they add no wrapper calls. |

The private UUID typing Protocol now exists only under `TYPE_CHECKING`, removing
runtime protocol-class creation. This affects module initialization rather than
the steady-state timing table.

The model optimization keeps a snapshot before invoking user deepcopy hooks.
Private mappings with custom behavior retain the old filtered-snapshot path.
Regressions cover hooks that mutate the original mapping, undefined private
attributes, custom constructor arguments, reducer overrides, exact UUID wire
bits, entropy request order, return class, and UUID safety metadata.

## Rejected shortcuts and limits

An initial atomic-container scan followed by a shallow C copy made integer
lists/sets around twice as fast, but slowed mixed containers by roughly 30% and
nested workloads by 6–12%. It was rejected. Direct dataclass dictionary transfer
showed inconsistent public-path gains and altered dict-subclass state behavior;
it was also rejected. Local/default atom bindings produced mostly noise. Manual
UUID slot construction would tie behavior to private stdlib implementation
details for a small real-entropy benefit; the portable constructor remains.
Generator construction avoided the intermediate tuple/frozenset lists but
measured 42%/21% slower on 1,000 integers; the existing list comprehensions stay.

Secure entropy calls, clock reads, synchronization, requested formatting, and
custom copy hooks remain required work. Removing these to improve a timing
would change the contract. The first copy of an unusual class pays protocol
inspection; subsequent copies use the existing cache.

Existing structural-clone tradeoffs remain: shared references are duplicated,
cycles incur the attempted structural walk before stdlib fallback, and datetime
values retain their existing atomic treatment (including the limitation with a
mutable custom timezone). Class admission does not track later monkeypatches to
copy protocols. A late `asdict` special-container fallback can repeat earlier
custom copy-hook effects. These pre-existing behaviors were not broadened into
an object-graph redesign during the performance audit.

Validation includes the full all-extras suite, the explicit UUID clock/entropy
chaos scenarios, benchmark coverage/validation gates, Ruff, formatting, strict
mypy, and suite inventory checks. Timing noise and the single measured
interpreter/platform limit the numerical conclusions; unchanged stdlib aliases
provide useful controls for that noise.

- `pytest --require-extras -q`: **6,048 passed**, 6 skipped, 165 deselected.
- Explicit UUID clock/entropy chaos selection: **12 passed**.
- `python -m bench verify --group clone --rounds 9`: all **six existing floors
  passed**, with noisy timing series and no skipped cases.
- Ruff, format checks, strict mypy, suite inventory, and `git diff --check` pass.

The 51 standard-only validation workloads also pass without installed optional
backends on Python 3.12.13, 3.13.14, and 3.14.6. Their
[profiles](clone-ids-stdlib-results.json) cover all 13 applicable Python functions
and explicitly mark the two optional model helpers as skipped. These are
validation profiles rather than cross-interpreter timing comparisons.
