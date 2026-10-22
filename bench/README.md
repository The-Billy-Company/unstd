# `bench` — the harness behind the speedup claims

Benchmark drivers and recorded measurements for `unstd` APIs, optional backends,
and bundled native kernels. Cases validate comparable outputs before timing;
reports retain gains, losses, and backend-specific limitations.

## Frozen function speeds

[`speeds.json`](speeds.json) freezes representative latency for every public
operation across all 18 packages. The shared registry currently has 224
workloads, including constructors, locally declared public methods/properties,
stdlib aliases, generator consumption and complete stream lifecycles. Nine
imported typing/enum/result vocabulary names have explicit exclusions. Private
implementation helpers are exercised through their public operations; they do
not each receive an independent latency budget.

```console
uv sync --locked --all-extras --group dev
uv run --no-sync python -m bench.speeds list
uv run --no-sync python -m bench.speeds check
uv run --no-sync python -m bench.speeds run --group serde
uv run --no-sync python -m bench.speeds verify --output /tmp/speeds-current.json
uv run --no-sync python -m tests.tooling run performance -- -q
```

Each registry `Case` names a callable, its bounded input and an independent
correctness checker. Checks run before and after timing, outside the measured
region. Workload notes describe setup included in the timer: for example,
stateful writers measure a fresh in-memory stream's complete lifecycle, while
filesystem and subprocess cases perform actual I/O. Timings include callable
dispatch overhead. These are representative input contracts, not worst-case or
all-input complexity guarantees; add `#variant` cases for other important shapes.

Each frozen row retains the observed nanoseconds, a ceiling with **30% headroom**,
iteration/round counts and sample spread. The estimator is the existing calibrated
minimum batch mean. A `noisy` annotation means spread exceeded 25%; inspect the
retained evidence and repeat on a quiet worker. Verification rechecks only cases
over their ceiling, at most twice, and retains each attempt in JSON reports.
A persistent slowdown still fails; a transiently delayed sample can recover
without changing its ceiling. Calibration confirms that its chosen iteration
count produces a long-enough batch twice, preventing one descheduled call from
selecting an undersized timer batch. JSON verification reports retain every raw
wall-clock batch mean and available thread-CPU samples for each attempt. CPU
time is diagnostic and never substitutes for the wall time being gated.
Existing ceilings and measured
evidence remain unchanged when `freeze` runs again, so it cannot turn an existing
slowdown green. New entries can be appended with:

```console
uv run --no-sync python -m bench.speeds freeze
```

Coverage gates discover public operations from source exports and runtime
bindings. New functions/packages need workloads and frozen rows; unknown IDs,
stale exclusions, changed workload notes and absent baseline entries fail.
Workload source fingerprints also detect fixture edits, including changed inputs
without a note change. Review those edits and run `freeze` to acknowledge the
new source snapshot; existing speed ceilings remain unchanged. A deliberately
different input should normally receive a new `#variant` ID.

Absolute speeds belong to a machine and runtime. Verification requires the
recorded CPU, OS, Python configuration, backend versions, thread settings and
native implementation (including C versus the optional Rust text kernel).
Missing prerequisites or mismatched evidence exit **2**; a speed regression exits
**1**; a complete passing comparison exits **0**. Correctness/coverage checks do
not compare wall-clock speeds and run in ordinary all-extra CI. Latency assertions
are in the explicitly selected `perf` lane.

For another worker, capture the comparison revision first using a separate file,
then retain that file when testing the candidate with identical workloads and
dependencies:

```console
uv run --no-sync python -m bench.speeds freeze --baseline /tmp/worker-speeds.json
# Switch/build the candidate revision, retaining the comparison file.
uv run --no-sync python -m bench.speeds verify --baseline /tmp/worker-speeds.json
```

The [performance workflow](../.github/workflows/performance.yml) automates this
for pull requests and manual reference comparisons: it builds each revision's
own wheel/native kernels, runs the current harness against both installations
on one worker, and retains baseline, measurements and Git identities even on
regression. It requires the comparison revision to implement the current workload
surface; an absent function or native implementation fails explicitly. New API
introductions need a comparison revision containing that API before a timing
comparison can succeed. The local committed snapshot remains separate from
ephemeral CI worker baselines.

For the random-workload investigation, use an alternating paired diagnosis:

```console
uv run --no-sync python -m bench.rand_diagnose --output /tmp/rand-diagnosis.json
```

This runs the original `crypto.bits`, `seed` and `shuffle` workloads beside
equivalent direct stdlib/NumPy controls with independent seedable engines. It
records primitive identities, loaded-source hashes, source agreement with Git
HEAD, the unchanged baseline hash, and all wall/CPU samples. A candidate-only
slowdown points toward implementation work. A similarly slow control makes the
runtime or host a suspect; it does not prove scheduler noise. In particular,
uniformly high thread CPU time is different evidence from wall time lost to
descheduling. Neither finding waives an absolute ceiling: diagnosis still exits
nonzero when the original candidate exceeds it.

## Stdlib speedup comparisons

The [parallel hashing report](crypto-hash-results.md) compares saved and current
BLAKE3 wrappers with `crypto_hash.py`, including all modes, tiny-call overhead,
serial streams, raw paired samples, and the measured host's scheduling noise.
The [follow-up tuning report](crypto-hash-tuning-results.md) measures the lower
4 MiB crossover and faster hex output against that initial implementation.
The [adaptive hashing report](crypto-hash-adaptive-results.md) covers the current
1 MiB crossover, bounded worker counts, and native batching of small stream chunks.
Saved snapshots must include `unstd/crypto` and its private helpers, which load
independently from the current source.

```console
python3 -m bench                 # measure everything, print a table
python3 -m bench --group serde   # one group
python3 -m bench verify          # exit non-zero if a claim fell through its floor
python3 -m bench update          # raise floors that genuinely improved
python3 -m bench --markdown      # the table shape a README wants pasted in
python3 -m bench list            # what is registered, without running it
```

Every backend has to be installed for a full run: `uv sync --all-extras`.

The `jsonx.dumps` and `jsonx.loads` 500-record cases measure fallback overhead.
These float-bearing/wide trees take stdlib codecs to preserve the JSON contract,
so their roughly 1× ratios are references, not native-backend speedup claims.
Their former floors (2.77× / 1.33×) covered incompatible value and error behavior;
they were reset to 70% of measurements on Python 3.13.14 when parity was fixed
(1.06× / 0.96×). Two eligible cases separately measure exact guarded orjson
paths: text encoding (5.98×) and integer-array decoding (1.91×), with floors at
70% of those Python 3.13.14 measurements. Guards are bounded because a Python
eligibility walk can outweigh the codec speedup on trees with many small values.

## What a number means

Each case is one accelerated `unstd` call and the stdlib call it stands in for,
over one payload. The headline is the **ratio** — stdlib cost divided by `unstd`
cost — not the absolute microseconds, which are a fact about whichever laptop
produced them.

`unstd` and `stdlib` columns are the least-contaminated per-call cost: the
minimum of several timed batches, with GC disabled for the duration. Noise on a
shared machine is one-sided (a preemption or a downclock can only make a sample
slower), so the fastest batch observed is the one least polluted by work that was
not the code under test. A case whose fastest and slowest batch differ by more
than 25% is flagged `noisy` — close your other work and run it again before
believing it.

Batch size is calibrated per callable rather than fixed. The cases here span four
orders of magnitude, and a 190 ns fuzzy-ratio and a 400 ms backtracking search
cannot share an iteration count without one of them drowning in loop overhead.

## Why the baseline pins floors, not values

`bench/baseline.json` holds a **floor** per case, set at 70% of the ratio that
was observed when it was recorded. A gate that demanded its own number back would
fail on every machine except the one that recorded it, and a gate that cries wolf
gets switched off within a week. A floor well under the observed ratio still
catches the thing worth catching: a backend that silently stopped being fast — an
accidental stdlib fallback, an option bitmask that stopped applying, a dependency
that regressed.

Floors move one way. `update` raises a floor when the measured ratio genuinely
improved and refuses to lower one, because lowering a floor to turn a red run
green is how a performance ratchet becomes a performance diary.

There is deliberately **no clamp at 1.0**. A case that measures below parity gets
pinned below parity — see the `pack` finding below. A floor is a statement about
what was observed, and rounding an honest 0.56× up to "at least as fast as the
stdlib" would make the baseline assert a win the code does not have, and then
fail on correct code forever.

## Two things the harness refuses to do

**Grade a missing backend as a pass.** A case names the backend its accelerated
path needs. Without it, `unstd` has already fallen back to the stdlib, both sides
of the comparison are the *same function*, and the 1.0× that comes out means
nothing. Those cases print as `skipped`, and `verify` exits 2 rather than 0 if
any case skipped — a green run with half the backends absent is worse than no run.

`rex` needs more than an import check, so it gets a custom probe: it degrades to
stdlib `re` per *pattern*, not per install, so the only honest question is what
`rex.compile` returned for this pattern. That probe also survives the backend
being swapped underneath the harness, which happened once already.

**Report a ratio between two functions that disagree.** Before any measurement,
every case marked like-for-like has both sides called once and their answers
compared; a mismatch aborts the run with the diff. One wrong `separators`
argument or one mismatched dtype turns a rigorous ratio into a fabricated one,
and it is the easiest mistake in the genre.

Cases where the two sides *legitimately* compute different things are marked
`[not like-for-like]` by `bench list` and carry a note saying so. irregex versus a
backtracking engine, BLAKE3 versus BLAKE2b, and a PCG64 bulk draw versus a
`random.randrange` loop are capability comparisons, not faster spellings of one
algorithm, and the note has to say which.

## Findings worth knowing

The harness earned its keep on the first run:

- **`pack_array` from a Python list is slower than `struct.pack`** (0.56×). This
  is structural, not a bug. `struct.pack` with a repeat-count format unboxes
  50 000 Python floats in a tight C loop writing straight into the output buffer;
  NumPy has to materialize an ndarray first. Nothing beats it at that job —
  `array.array` and `np.fromiter` were both measured and both lose too. The seam's
  real value is the shape beside it: handed an ndarray it already owns,
  `pack_array` is a memcpy and wins **159×**, and `unpack_array` returns a
  zero-copy view for **1667×**. Hold your data in an array, not a list.
- **`digest.raw`'s honest twin is `blake2b`, not `sha256`.** BLAKE2 is what
  `hashlib` ships from the BLAKE family, so it is the thing `digest.raw` stands in for,
  and BLAKE3 beats it 1.7×. `sha256` is not a fair comparison in either
  direction: every ARMv8 and modern x86 core implements it in silicon, so
  `hashlib.sha256` is a hardware instruction and beats single-threaded BLAKE3
  (350 µs vs 453 µs on an M-series laptop). That is a fact about the chip, not
  the algorithm, and it is not something a caller can act on.
- **`clone.deep` on a small dict is 1.19×, not the 1.8× the README claimed.**
  The floor case is real and the README now says what the harness measures.
- **The 1.0.5 `clone.deep` floors were set against a clone that dropped types.**
  Its msgspec round-trip turned a nested `StrEnum` into a `str` and a
  `defaultdict` into a `dict`, and its equality guard could not tell. 1.1 replaced
  it with a type-exact walker (4.4× on the large trees instead of 5.8×, 4.1× on
  the small dict instead of 1.2×), and the two large-tree floors were re-pinned
  by hand at the usual 70% of the new measurement. That is the one legitimate way
  a floor comes down: the old number was bought with a bug.
- **`update --group X` used to erase every other group's floors.** It rewrote the
  baseline from the narrowed run alone. It now merges, and a test pins that.

## Adding a case

One `case(...)` call in `bench/cases.py`, then `python3 -m bench update` to pin
its floor. Bind the callables at module import — a `__import__` or attribute walk
inside the measured lambda adds a few hundred nanoseconds per iteration, which is
invisible on the 1 MiB hash case and roughly the entire measurement on the small
dict clone.

Mark `equivalent=False` if the two sides do not compute the same answer, and say
why in `note`. If importability does not prove the fast path was taken, pass a
`probe`.

## Typed NDJSON ingestion

Run `uv run --locked --all-extras python -m bench --group serde --rounds 5` to include
`ndjson.loads · 1k typed trace rows`. It compares a reused `structs.Codec` with
per-line `jsonx.loads` followed by `msgspec.convert` into the same nested
dataclass schema. Both paths validate every row; the audit checks identical
results before timing. Payload construction and codec setup stay outside the
measurement. The 1,000-row payload contains UTF-8 text, tags, attributes, and
nested token usage; it measures ingestion with validation, not parsing alone.

The historical relative floor currently fails on the measured Python 3.13
environment. A [paired diagnosis](typed-ndjson-floor-results.md) found the typed
reader substantially unchanged while its dictionary-decoding comparator became
about 3.1× faster. The original floor remains intact; this ratio compares two
current ingestion strategies and cannot independently establish a typed-reader
throughput regression.

## Complete Serde function profiles

`serde_profile` exercises every runtime Python function in `unstd.serde`, its
stdlib aliases, and the typed codec's directly bound native methods. It checks
outputs, including types and dictionary order, before timing. Profiling and
timing run separately so profiler overhead is excluded from workload timings.

```console
uv run --no-sync python -m bench.serde_profile --output /tmp/serde.json
uv run --no-sync python -m bench.serde_profile --compare-source /tmp/before/src --rounds 9 --repetitions 3 --output /tmp/serde-comparison.json
uv run --no-sync python -m bench.serde_profile --compare-source /tmp/before/src --paired --rounds 9 --repetitions 3 --output /tmp/serde-paired.json
uv run --no-sync python -m bench.serde_profile --fallback --output /tmp/serde-fallback.json
```

The comparison source must contain `unstd/`; workers verify the actual import
path and preserve their selected source between repetitions. Save that source
before editing, or extract it from the Git revision under comparison. Workers
alternate old/new order and reject any changed public signature. `--fallback`
disables orjson and pybase64 dispatch; typed codecs still require msgspec, and
NumPy extension workloads run when NumPy is installed. The complete native
profile also requires all three Serde backends. Declaration-only Protocol
methods and overloads contain no runtime work and are excluded from coverage.

`--paired` is a companion timing mode that loads separate original/current
module namespaces in one interpreter and alternates them for each workload.
This reduces long-run scheduling and clock drift; it produces timing samples
rather than cProfile data. Use the ordinary worker mode for complete function
profiles and signature comparisons.

The measured audit and retained implementation choices are recorded in
[`serde-results.md`](serde-results.md), with per-function profiles and all
workload timings in [`serde-results.json`](serde-results.json). These are local
measurements, not a proof that one implementation is fastest for every input,
interpreter, backend version, or machine.

## Complete clone and UUID profiles

`clone_ids_profile` checks and profiles all 15 runtime Python functions in
`unstd.clone` and `unstd.ids`, plus their four direct stdlib aliases. Its 54
workloads cover atomic and mixed containers, dataclasses, models, reducers,
cycles, custom factories, native UUID minting, and the handwritten fallback's
clock/counter branches. Outputs are checked for type, key order, mutable
reference isolation, UUID layout, and unchanged public signatures before timing.

```console
uv run --no-sync python -m bench.clone_ids_profile --output /tmp/clone-ids.json
uv run --no-sync python -m bench.clone_ids_profile --compare-source /tmp/before/src --rounds 9 --repetitions 3 --output /tmp/clone-ids-comparison.json
uv run --no-sync python -m bench.clone_ids_profile --stdlib-only --profile-only --output /tmp/clone-ids-stdlib.json
python bench/clone_ids_profile.py --stdlib-only --profile-only --output /tmp/clone-ids-stdlib.json
```

The saved source must contain `unstd/clone/structural.py` and `unstd/ids/uid.py`.
Sources load into separate namespaces, and comparison order alternates for each
workload. cProfile runs separately from timing. Raw profiler entries are summed
by source location so native/fallback namespaces cannot overwrite each other's
function counts. Declaration-only Protocol methods are excluded.

Full coverage needs pydantic and msgspec; `--stdlib-only` explicitly skips their
two helpers and selects the stdlib UUID generator. The handwritten fallback is
always measured separately, including on Python 3.14+. Cold-cache and counter
branch workloads include their stated state-reset work; ordinary UUID workloads
use real clocks and entropy. The function inventory fails on an uncovered
runtime function instead of silently reporting complete coverage.
Direct script execution also avoids loading other benchmark groups and their
optional dependencies through `bench.__init__`.

The implementation decisions and measured limits are in
[`clone-ids-results.md`](clone-ids-results.md); complete profiles, source hashes,
public signatures, backend versions, and timing samples are in
[`clone-ids-results.json`](clone-ids-results.json).
Standard-only validation profiles for Python 3.12, 3.13, and 3.14 are saved in
[`clone-ids-stdlib-results.json`](clone-ids-stdlib-results.json).

## Bundled native kernel profiles

`native_profile` compares a saved source tree with the current bundled C
implementations of structural cloning, Levenshtein distance, and Jaro matching.
Save the original sources before editing; the directory must contain `unstd/`.
The measured comparison functions must be Python-only; native-enabled baselines
are rejected to avoid importing current kernels into the comparison. Build the
current extensions before running the driver.

```console
uv run --no-sync python -m bench.native_profile --compare-source /tmp/before/src --rounds 9 --repetitions 3 --output /tmp/native-comparison.json
python bench/native_profile.py --compare-source /tmp/before/src --profile-only --output /tmp/native-profile.json
```

Text workloads explicitly select the base-install Python wrappers, which may
dispatch into the bundled kernels even when rapidfuzz is installed. The original
installed text bindings are recorded. The driver compares public signatures
both before and after base-wrapper selection, and checks
output types, dictionary order, mutable independence, shared-branch behavior,
and cyclic fallback before timing. It covers small and 200/500-record clones,
custom copy protocols, Unicode distance at lengths 12/64/65/256/1024, short and
75-character Unicode name matching, and sequence/string-subclass fallbacks.

Python and C cProfile entries are retained separately from timing. Per-workload
profiles and untimed return-value tracking prove all three native kernels
accept their workloads and the declared Python fallback routes remain selected.
A C call returning `NotImplemented` cannot prove acceleration. Missing native
modules or unexpected routes fail the
run. Paired measurements alternate original/current order by repetition. JSON
includes all timing samples, harness/source/extension hashes, public signatures,
interpreter/machine details, and native dispatch evidence. Unit tests verify the
evidence checks without enforcing machine-specific speedup thresholds.

The measured results, safety boundaries, validation and known fallback overhead
are recorded in [`native-results.md`](native-results.md), with complete evidence
in [`native-results.json`](native-results.json).

The separate [Rust text experiment](rust-text-results.md) compares the same
base-install Python calls backed by C and by an explicitly selected Rust
prototype. Default builds retain C because this prototype adds short-Jaro
latency and binary-size overhead. `text_native_compare.py` records paired
samples, exact output and route checks, hashes, binary sizes and approximate
fresh-process RSS. Build instructions are in
[`native/text/README.md`](../native/text/README.md).

## Gzip throughput and size

Run `uv run --locked --all-extras python -m bench --group compress --rounds 5`.
The three cases use 4,000 JSONL trace rows (1,115,230 uncompressed bytes), explicit
compression level 6 and `mtime=0`. Decompression receives identical bytes from
stdlib's compressor. The stream case runs the complete in-memory NDJSON dump,
gzip write, gzip read and NDJSON load, using the same NDJSON implementation on
both sides. Before timing, both compressors' output is independently decoded
back to the original bytes, and both pipelines must recover the original rows.
Compressed-byte equality is deliberately not required.

On macOS arm64, Python 3.12.13 and zlib-ng 1.0.0 (2026-10-18), the bounded
five-round run measured:

| Operation | Native MiB/s | Stdlib MiB/s | Stdlib/native time ratio |
|---|---:|---:|---:|
| One-shot compression | 716 | 330 | 2.17× |
| One-shot decompression | 4,257 | 7,648 | 0.56× |
| Full NDJSON stream roundtrip | 27.7 | 29.7 | 0.93× |

Throughput counts the uncompressed input once, including for the roundtrip.
One-shot compressed sizes were **40,135 bytes native / 48,096 bytes stdlib**;
streamed sizes were **40,111 / 48,096 bytes**. Codec choices affect storage size
as well as speed. Compression and roundtrip timing were flagged noisy by the
harness; decompression was stable. Compression improved on this workload,
while decompression was slower and the complete pipeline had no measured gain.
The committed floors retain those observed losses rather than asserting parity.
