# Math for repeated array work

Eight operations we found in Consumer's production calculations. We select fewer
scores, reduce rows without building squared or weighted matrices, and share
audio samples instead of copying every overlapping frame.

```sh
pip install 'unstd[math]'
```

```python
from unstd import math as numeric

best = numeric.topk(scores, 20)  # descending positions; stable ties
unit = numeric.normalize_rows(embeddings, eps=1e-9, dtype="float32")
similarity = numeric.cosine_rows(embeddings, query, dtype="float32")
pooled = numeric.weighted_mean(embeddings, weights, dtype="float32")
centroids = numeric.grouped_mean(embeddings, labels, groups=128)
correlation = numeric.pearson(observations[:-1], observations[1:])

frames = numeric.frame(audio, length=400, hop=160)
energy = numeric.mean_square(frames, dtype="float32")
# RMS: np.sqrt(energy). Audio dB: 10 * np.log10(energy + 1e-12).
```

## Where each helper earns its place

| Operation | Production use | Work avoided |
| --- | --- | --- |
| `topk(values, k)` | Resident memory retrieval | Sorting all N scores when only K positions are needed |
| `normalize_rows(values, *, eps=0, dtype="float64")` | Speaker clustering | N×D squared-norm temporary |
| `cosine_rows(values, query, *, eps=0, dtype="float64")` | Domain relevance scoring | N×D normalized gallery allocation |
| `mean_square(values, *, dtype="float64")` | Audio quality and VAD energy | N×D squared-sample temporary |
| `pearson(x, y)` | Presence lag-one correlation | General covariance matrix calculation |
| `weighted_mean(values, weights, *, dtype="float64")` | Generation embedding pooling | N×D weighted-value temporary |
| `grouped_mean(values, labels, groups)` | Domain and speaker centroids | Repeated whole-label scans and selected-row copies |
| `frame(values, length, hop)` | Speaker audio analysis | N×W integer indices and copied overlapping samples |

The [proof report](../../../bench/reports/math/README.md) maps all eight to
source files and lines, includes raw timing and allocation results, and records
losing comparisons. These are local measurements of production-shaped NumPy
expressions. The package does not change Consumer's deployed callers or replace its
existing native kernels. We found no pandas calculation hotspot worth inventing
a dataframe abstraction around; numeric columns can use these array operations.

## Small calls and platform dispatch

CPython wheels include an optional C implementation for small ndarray reductions,
stable selection, grouped means and frame views. It uses Python's checked buffer
protocol without NumPy headers or a NumPy C ABI dependency. Large row reductions
retain NumPy's vectorized loops; gapped column views use a layout-specific path.
Those paths may materialize a contiguous copy or squared matrix when that
protects throughput. Temporary-free reductions describe the optimized layouts.

Small top-k requests use a bounded heap; requests retaining much of the vector
use NumPy's stable sort. Grouped means accumulate rows once instead of repeatedly
selecting each group. These decisions use input shape and layout, with common
cutoffs verified on macOS and Linux rather than startup timing or CPU-specific
compiler flags.

The [edge and workflow proof](../../../bench/reports/math/edges.md) compares the
complete public call against NumPy and the initial implementation, with matched
calculation precision. It retains cold first-call timing, raw samples and every
losing comparison. Unsupported inputs and unavailable native builds retain the
portable implementation; its speed is not guaranteed to match a compiled wheel.

## Numeric contract

Inputs are real integer or floating array-likes. Strings, object arrays, complex
arrays and booleans are rejected. All helpers check dimensions and leave their
inputs unchanged; strided and read-only arrays work. NumPy is optional at import
time; calling an operation without it raises an error naming `unstd[math]`.

Calculations default to float64. Normalization, cosine, mean-square and weighted
pooling also accept `dtype="float32"`, which selects float32 multiplication,
accumulation and output. Choose that explicitly for float32 production pipelines;
widening their arithmetic can cost throughput. Integers are converted before
multiplication. Nonfinite data propagates through numeric calculations; it is
not silently omitted. Ordinary floating overflow and underflow still apply.
Exceptional native arithmetic defers to NumPy so `errstate` warning, exception
and callback policies remain effective.

`topk` accepts a vector and `0 <= k <= N`, returns `intp` indices, and resolves
equal values by their original position. It preserves exact integer ordering,
including uint64 values above 2⁵³ and signed minima. NaNs are rejected, even for
`k=0`; infinities are ordered. The partition path costs O(N + K log K), native
heap selection costs O(N log K), and dense selection uses a full stable sort.

Normalization and cosine accept matrices, with one query vector for cosine.
The norm floor is additive: `norm + eps`, with finite, nonnegative `eps`. Zero
rows normalize to zero; zero-vector cosines are zero. For a pre-normalized
gallery, keep the direct `gallery @ query` operation.

Mean-square reduces each matrix row. A zero-width row gives NaN. Pearson takes
equal-length vectors, centers around a nearby origin for large-offset accuracy,
and gives NaN for fewer than two observations or a constant vector. Preserve
the caller's own policy if an undefined correlation should become zero.

Weighted pooling takes a matrix and one finite, nonnegative weight per row;
their total must be finite and positive at the chosen precision. Grouped means
take a matrix and one integer label per row in `[0, groups)`. Output rows follow
label order, with zeros for absent groups; callers needing counts can use
`np.bincount(labels, minlength=groups)`. This differs from the domain caller's
empty-array sentinel. The native accumulator also handles small group counts;
without it, those counts can favor the existing mask loop.

## Framing and ownership

`frame` takes a vector and positive integer length/hop, retains the source dtype,
and includes only complete windows. Short inputs give shape `(0, length)`.
For ndarray inputs the result is a read-only view: later source edits remain
visible. Use `.copy()` when independent mutable frames are required. List inputs
allocate one source array during conversion.

Contiguous views use checked extents and a read-only buffer owner retaining
the source. Other layouts use NumPy's checked
[`sliding_window_view`](https://numpy.org/doc/stable/reference/generated/numpy.lib.stride_tricks.sliding_window_view.html).
Creating a view saves allocations; downstream processing still reads its frame
values. The proof measures framing plus energy as well as view construction.

## Verification

```sh
python -m pytest tests/suites/product/math -q
python -m bench.profiles.math --output bench/reports/math/results.json
python -m bench.profiles.math_workflows --require-native --strict --output /tmp/math-workflows.json
python -m bench.speeds check --group math
```

Unit and property tests compare independent scalar references, exact correlation
fractions, stable sorting and production NumPy expressions. They cover malformed
inputs, integer extremes, both calculation precisions, empty shapes, strides,
nonmutation, shared frame storage and an isolated process without NumPy.
