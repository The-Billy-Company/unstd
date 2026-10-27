Add `unstd.math` and the optional `math` extra: eight NumPy operations for stable
top-k retrieval, row normalization, cosine scoring, mean-square energy, Pearson
correlation, weighted pooling, grouped centroids and read-only audio frame views.
Include explicit float32/float64 calculation precision, independent correctness
tests, installed-artifact probes and reproducible timing/allocation comparisons.
Specialize small calls, dense top-k selection, grouped means and frame views
with optional portable C kernels, retaining NumPy for larger reductions and
exceptional arithmetic policies. Add matched-precision workflow regression
guards for macOS and Linux.
