# Math package evidence

The results below describe the initial implementation. The follow-up
[edge and workflow proof](edges.md) covers the optimized native dispatch,
small/few-group cases, complete workflows and both macOS and Linux.

Eight production calculation patterns motivated `unstd.math`. On this worker,
each helper improves at least one measured shape. The package is not a blanket
replacement for NumPy: small arrays and float32 inputs have material losses.
No Consumer production callers were changed by this package addition.

## Reproduce

```sh
python -m bench.profiles.math --output bench/reports/math/results.json
```

The [raw report](results.json) contains all 45 cases, every timing sample,
iteration counts, allocation peaks, maximum absolute output error and source
hashes. The [benchmark](../../profiles/math.py) contains the complete baseline
expressions. No source data, customer data or simulated delays are used.

## Eight concrete use cases

Paths below are relative to the adjacent `consumer` checkout inspected for this
change. They identify actual production calculations, not invented dataframe
workloads. We found no substantive pandas calculation hotspot; existing Evaluation
Polars aggregations already use native grouping.

| Helper | Production calculation | Change |
| --- | --- | --- |
| `topk` | `services/ai/core/domain/knowledge/memory/resident/index.py:672,728`: stable full descending sort, then slice | Partition to find the cutoff; sort only selected values; preserve first-position ties. |
| `normalize_rows` | `services/speaker/speaker_plane/core/clustering.py:174,204,259,305`: `X / (norm(X, axis=1, keepdims=True) + 1e-9)` | Row dot reductions avoid a squared matrix; preserve the additive epsilon formula. |
| `cosine_rows` | `libs/biology/substrate/dynamics/free_energy.py:386–397`: normalize a complete matrix, then score a query | Compute row norms and dot products without allocating the normalized matrix. This targets the portable fallback; the existing native path already has a fused kernel. |
| `mean_square` | `services/speaker/speaker_plane/core/signal.py:104–107`: `10*log10(mean(frames**2, axis=1) + 1e-12)` | Eliminate the squared-frame temporary. The caller applies its existing dB conversion. |
| `pearson` | `services/ai/core/domain/presence/soma.py:294–299`: `corrcoef(values[:-1], values[1:])[0,1]` | Direct centered reductions avoid constructing a general covariance matrix. Keep the caller's small-series and zero-variance policy. |
| `weighted_mean` | `services/ai/agent/marionette/runtime/interleave/stream.py:207–219`: `average(vectors, axis=0, weights=weights)`, then normalize | Contract the weights with rows without a weighted matrix. The benchmark includes the actual list-conversion cost. |
| `grouped_mean` | `libs/biology/substrate/dynamics/free_energy.py:419–432` and `services/speaker/speaker_plane/core/clustering.py:265–274`: repeatedly mask and select each group | Column-wise bincount reductions avoid scanning all labels once per group. Missing groups explicitly yield zero rows. |
| `frame` | `services/speaker/speaker_plane/core/signal.py:16–22`: build an integer index matrix and copy overlapping samples | A read-only sliding view avoids both allocations. Source edits remain visible; callers needing independent mutable frames must copy. |

## Measured latency

Python 3.14.9, NumPy 2.5.3,
macOS 26.6, ARM64. Times are median microseconds per call. A speedup above 1×
means the helper is faster. Float32 rows compare the production expression's
float32 arithmetic with the helper's default float64 arithmetic. The final
column explicitly selects `dtype="float32"`, retaining the production calculation
precision. Helpers without this option show a dash. These opt-in rows use the
same inputs and baseline expressions as their default counterparts.

| Calculation | Shape | Float64 NumPy → helper, µs | Float64 speedup | Float32 input, default helper | Explicit float32 calculation |
| --- | --- | ---: | ---: | ---: | ---: |
| Stable top-k | 100,000 scores; k=16 | 6044.2 → 597.6 | 10.11× | 9.31× | — |
| Row normalization | 4,096×128 | 304.0 → 243.3 | 1.25× | 0.42× | 1.72× |
| Row cosine | 4,096×128 | 361.6 → 198.6 | 1.82× | 0.47× | 2.31× |
| Row mean-square | 4,096×128 | 151.2 → 78.2 | 1.93× | 0.51× | 3.14× |
| Pearson | 50 observations | 10.0 → 6.3 | 1.57× | 1.93× | — |
| Weighted mean | 4,096×128 | 262.8 → 79.6 | 3.30× | 0.99× | 2.73× |
| Grouped means | 20,000×32; 128 groups | 2891.5 → 1573.3 | 1.84× | 2.04× | — |
| Frame construction | 160,000 samples; width=400; hop=160 | 292.6 → 4.0 | 72.54× | 49.16× | — |

Framing is not only a construction win. Framing **and computing row energy**
together improve from 411.6 to
97.6 µs for float64
(4.22×) and from
415.7 to
235.4 µs for float32
(1.77×). With explicit float32
calculation, the same float32 framing/energy workflow takes
53.4 µs
(8.98×).

## Limits and losses

- Float64 remains the default calculation contract, including float32 input.
  The widened contractions can be materially slower than the original float32
  NumPy arithmetic. Explicit `dtype="float32"` avoids that widening for
  normalization, cosine, mean-square and weighted pooling. Both choices are
  measured, including cases where the explicit option still loses.
- Float32 bulk cosine and mean-square still use about 13× less traced peak
  allocation. Float64 bulk cosine uses about 41× less; weighted pooling about
  942× less. Normalization still needs its full output matrix.
- On 64 scores, NumPy's full sort is about 4–5× faster than `topk` validation
  and partitioning. Small matrix normalization and cosine also lose.
- With four groups over 2,000 rows, grouped means are slower: about 2.2× for
  float32 and 2.0× for float64. The 128-group case wins, but this does not prove
  a benefit for the current small fixed domain counts. Column-wise bincounts
  can use more peak memory than one-at-a-time selection; float32 promotion
  further increases that cost.
- Weighted pooling from 64×64 Python lists changes by only 1.12–1.13×.
  List conversion dominates. The much larger array-input win should not be
  projected onto this list-based caller.
- `frame` is a view with a deliberate ownership difference from the existing
  float32 copy. Its construction speedup is not an end-to-end model speedup.
- These are isolated synthetic expression measurements on one worker, not
  deployed service latency, actual request frequency or comparisons with
  Consumer's existing principia native kernels. They do not justify replacing
  already optimized paths or changing caller policies silently.

## Method and numerical agreement

Seeded small and bulk fixtures cover float32 and float64, repeated score ties,
a zero embedding row, 50-observation series, array and list pooling, four and
128 groups, and overlapping frames. We verify complete outputs before timing
and after every paired batch. Sort indices and frame contents match exactly.
Float32 baselines use `rtol=4e-6, atol=2e-7`; float64 baselines use
`rtol=atol=1e-12`. The raw report records actual errors rather than only pass/fail.

Each operation includes public validation, conversion and output allocation.
Nine paired wall-time rounds alternate baseline/helper order. A bounded
calibration selects batches near two milliseconds, and samples record time per
call plus the batch iteration count. Allocation peaks come from separate
`tracemalloc` passes with the returned result retained. They measure traced
allocations, not process RSS or resident service memory.

The run rejects source or benchmark changes during measurement. Its source
SHA-256 is `53c981c52cf0206ddeb3878178522c6ea8cd14a38df3ba041f4fa4cc8d947adc`. Platform, interpreter, NumPy and
thread environment settings are retained with the raw evidence.

The domain fallback regularizes row norms and separately guards and normalizes
the query. `cosine_rows` applies its documented additive `eps` to both norms;
the benchmark compares that formula on both sides. Adopting it at that source
requires preserving the caller's query guard and choosing the intended norm
floor policy. It is not a byte-for-byte replacement of the whole domain scorer.

## Package checks

The final implementation passes 85 unit/property tests, four isolated installed
wheel/sdist and missing-extra probes, the math public API contract, Ruff and
strict mypy. The 45 expression comparisons above verify every complete output
before and after paired timing batches.

[`speeds.json`](speeds.json) records eight standard speed-registry workloads
using the shared timer. Their historical rows were appended to the main baseline
with the actual recording environment and source hash; existing rows and source
fingerprints were preserved. At verification time the global speed check was
blocked by concurrent changes to `speed_cases_operations.py` and four new
dataset APIs without frozen rows. Math's own fingerprint and eight rows match.

## Complete measurements

Allocation ratio is baseline peak / helper peak; below 1× means the helper
allocated more. Every losing row is retained.

| Case | NumPy, µs | Helper, µs | Speedup | Allocation ratio |
| --- | ---: | ---: | ---: | ---: |
| `topk/float32/64` | 1.1 | 4.4 | 0.24× | 0.71× |
| `topk/float32/100000` | 6066.5 | 651.6 | 9.31× | 3.98× |
| `normalize_rows/float32/8x64` | 2.4 | 4.8 | 0.49× | 0.39× |
| `cosine_rows/float32/8x64` | 5.2 | 8.2 | 0.63× | 0.51× |
| `mean_square/float32/8x64` | 2.2 | 1.6 | 1.37× | 0.42× |
| `normalize_rows/float32/8x64/compute32` | 2.4 | 3.8 | 0.63× | 0.86× |
| `cosine_rows/float32/8x64/compute32` | 5.2 | 7.6 | 0.68× | 1.83× |
| `mean_square/float32/8x64/compute32` | 2.5 | 1.5 | 1.68× | 1.90× |
| `normalize_rows/float32/4096x128` | 227.9 | 548.4 | 0.42× | 0.49× |
| `cosine_rows/float32/4096x128` | 234.1 | 500.1 | 0.47× | 12.83× |
| `mean_square/float32/4096x128` | 144.7 | 286.4 | 0.51× | 13.15× |
| `normalize_rows/float32/4096x128/compute32` | 222.7 | 129.4 | 1.72× | 1.00× |
| `cosine_rows/float32/4096x128/compute32` | 199.9 | 86.6 | 2.31× | 38.14× |
| `mean_square/float32/4096x128/compute32` | 128.0 | 40.7 | 3.14× | 64.18× |
| `pearson/float32/50` | 20.1 | 10.4 | 1.93× | 1.43× |
| `pearson/float32/4096` | 33.3 | 22.2 | 1.50× | 1.69× |
| `weighted_mean/float32/64x64/list` | 99.2 | 88.0 | 1.13× | 2.84× |
| `weighted_mean/float32/4096x128/array` | 203.9 | 206.2 | 0.99× | 21.01× |
| `weighted_mean/float32/4096x128/array/compute32` | 186.6 | 68.3 | 2.73× | 42.16× |
| `grouped_mean/float32/2000x16/4` | 51.5 | 110.7 | 0.46× | 0.28× |
| `grouped_mean/float32/20000x32/128` | 3456.3 | 1694.5 | 2.04× | 0.02× |
| `frame/float32/3200` | 8.3 | 5.1 | 1.63× | 75.81× |
| `frame_energy/float32/3200` | 15.1 | 12.7 | 1.19× | 1.49× |
| `frame_energy/float32/3200/compute32` | 15.9 | 10.4 | 1.52× | 45.42× |
| `frame/float32/160000` | 356.1 | 7.2 | 49.16× | 2042.96× |
| `frame_energy/float32/160000` | 415.7 | 235.4 | 1.77× | 34.26× |
| `frame_energy/float32/160000/compute32` | 479.3 | 53.4 | 8.98× | 444.04× |
| `topk/float64/64` | 1.0 | 4.2 | 0.25× | 0.73× |
| `topk/float64/100000` | 6044.2 | 597.6 | 10.11× | 2.50× |
| `normalize_rows/float64/8x64` | 2.3 | 4.3 | 0.54× | 0.92× |
| `cosine_rows/float64/8x64` | 3.9 | 5.5 | 0.72× | 3.07× |
| `mean_square/float64/8x64` | 1.7 | 1.3 | 1.27× | 2.88× |
| `normalize_rows/float64/4096x128` | 304.0 | 243.3 | 1.25× | 1.00× |
| `cosine_rows/float64/4096x128` | 361.6 | 198.6 | 1.82× | 40.72× |
| `mean_square/float64/4096x128` | 151.2 | 78.2 | 1.93× | 63.59× |
| `pearson/float64/50` | 10.0 | 6.3 | 1.57× | 1.28× |
| `pearson/float64/4096` | 26.6 | 15.9 | 1.68× | 1.96× |
| `weighted_mean/float64/64x64/list` | 71.8 | 64.1 | 1.12× | 2.84× |
| `weighted_mean/float64/4096x128/array` | 262.8 | 79.6 | 3.30× | 941.50× |
| `grouped_mean/float64/2000x16/4` | 47.7 | 95.9 | 0.50× | 8.09× |
| `grouped_mean/float64/20000x32/128` | 2891.5 | 1573.3 | 1.84× | 0.77× |
| `frame/float64/3200` | 7.3 | 3.9 | 1.84× | 75.81× |
| `frame_energy/float64/3200` | 11.5 | 7.2 | 1.61× | 44.60× |
| `frame/float64/160000` | 292.6 | 4.0 | 72.54× | 2723.90× |
| `frame_energy/float64/160000` | 411.6 | 97.6 | 4.22× | 340.18× |
