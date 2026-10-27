# Complete project assembly alternative

The broader current project CPU pipeline also misses 100×. A private prototype
that reuses each admitted immutable response and assembles the final index once
measured **4.61–9.96× median** on macOS. This experiment adds no production API
and changes no application files. It has no Linux measurement.

The read-only source audit found three numeric stages in the current remote
project flow: `remote._validate` constructs and discards a token index before
returning a response; `_remote_index` constructs the per-document index again;
the final project assembly concatenates all token arrays and constructs another
index. The retained replay compiles the actual result class, validator, caller
guards, bookkeeping and final assembly from a private source capture. Reviewed
AST gates reject changes to the admission predicate, snapshots and assembly
seam. Public [provenance and semantic checks](project-validation.json) retain
hashes without publishing the captured application definitions or identity.

The candidate calls the existing `RaggedDecoder` once per response with finite
validation and strict boundaries, writes the validated immutable arrays to
`response.extensions`, then consumes that handoff in the caller. The existing
caller geometry and publication guards remain. It joins immutable token bytes
once, constructs cumulative immutable boundaries, and allocates the exact same
captured frozen result class with `_device=None`. A single document can retain
its original validated bytes directly. The final private trusted construction
relies on prior immutable admission; it does not accept arbitrary external
arrays as trusted inputs.

Each response is validated before the next response is consumed. Deferring all
validation until a batch finishes would change the current error, breaker and
callback order. Every timed traversal repeats complete validation; the fixture
bytes are reused, while validation and decoded results are never cached.

| Documents × rows per document | Baseline → candidate | Median gain | Observed conservative gain |
| --- | --- | --- | --- |
| 1 × 1 | 9.895 → 1.681 µs | 5.89× | 5.33× |
| 1 × 512 | 47.690 → 4.786 µs | 9.96× | 9.72× |
| 32 × 1 | 218.852 → 38.877 µs | 5.63× | 5.21× |
| 32 × 32 | 293.098 → 52.328 µs | 5.60× | 5.31× |
| 32 × 128 | 518.656 → 91.685 µs | 5.66× | 4.95× |
| 256 × 1 | 1,779.904 → 310.783 µs | 5.73× | 5.22× |
| 256 × 32 | 2,351.363 → 426.358 µs | 5.52× | 5.09× |
| 256 × 512 | 10,459.459 → 2,269.938 µs | 4.61× | 2.31× |

The [raw samples](project-macos.json) use nine alternating rounds with 25 ms
target batches on macOS 26.6 arm64, CPython 3.14.9 and NumPy 2.5.3. The native
validator is the same source-checkout binary used by the other macOS
alternatives. Conservative gain is the fastest baseline sample divided by the
slowest candidate sample. All eight cases fail both 100× checks.

The clock includes first admission, the response ownership handoff, caller
guards, cumulative budget/boundary bookkeeping, final array construction,
exact result-class allocation and reference-count cleanup. Request/response
metadata setup, source capture and output oracles are excluded, as are network,
model inference, callbacks, asyncio/offload scheduling, breaker state and
logging. This is a complete synchronous numeric pipeline comparison, not a
claim about total request latency.

Nine valid cases, including empty input and 256 documents with 512 rows each,
check all token bytes and boundaries, exact dtypes/shapes/class, immutable byte
owners, frozen fields, byte counts, device state and expected source sharing.
Thirty-three invalid cases check exception messages and stopping order for
header identity, truncated headers, partial rows, boundaries, publication
allowances, late NaN/infinities and empty identity. The legacy first admission
accepts eight zero bytes before the later caller rejects its short header; the
candidate preserves that stage order. Numeric rejection invokes the original
validator only on the error path to preserve legacy exception messages; those
fallbacks are disclosed and counted, and invalid paths carry no speedup claim.
Untimed native observation confirms eight full validations across two repeated
three-response traversals and a traversal rejected at its second response.

The evidence supports retaining validated immutable ownership between existing
stages. A generic composition API would additionally need an admitted-frame
handle and a contract for compatible layouts, boundary rebasing, overflow and
ownership. The current public tuple result does not certify arbitrary arrays
for unchecked composition. This experiment does not establish a need to add
that API to reach 100×, and no such API was added.

To reproduce from an authorized Consumer checkout, copy the exact retained
[probe](project_probe.py.txt) into `.local/pack-project-replay.py` at the unstd
repository root, build the current native validator, and run:

```console
python .local/pack-project-replay.py --consumer-root ../consumer --run-timings --rounds 9 --batch-ms 25 --output .local/pack-project-macos.json
```

The probe creates a private capture if absent and otherwise rejects source
drift. Its public provenance records the original source/capture and probe
hashes. The private application definitions remain outside release inputs.
