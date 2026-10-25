# Frozen performance baselines

`baseline.json` holds stdlib comparison floors for `python -m bench`. `speeds.json` holds absolute latency ceilings and workload fingerprints for `python -m bench.speeds`. Moving these files does not change their evidence or thresholds.

`speeds.json` also preserves additive historical captures. Its aggregate runtime
describes the original rows; later additions retain their own capture records.
The four SortedList operation variants explicitly carry their CPython 3.14.9
environment and provenance per row, alongside full raw samples in
`containers_addition_provenance`. Existing rows and ceilings remain unchanged.
Verification rejects an incompatible selected captured row. Whole-registry
revision comparisons must freeze a fresh, separate baseline on the comparison
worker with the same interpreter and dependencies as the candidate.

See the [benchmark guide](../README.md).
