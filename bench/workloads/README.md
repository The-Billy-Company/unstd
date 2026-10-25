# Latency workloads

`speed_cases_codecs.py`, `speed_cases_operations.py` and `speed_cases_runtime.py` own output-checked fixtures for [`../speeds.py`](../speeds.py). Their logical fingerprint keys remain stable when files move; changes to fixture bytes still require review.

See the [benchmark guide](../README.md).

The rand receipts in [`billy_rand.json`](billy_rand.json),
[`rand_keyed.json`](rand_keyed.json), and
[`rand_strategies.json`](rand_strategies.json) retain checked consumer source,
AST/configuration hashes and synthetic input sizes. The drivers distinguish
unchanged output/state comparisons, explicitly different SplitMix64 count
batches, and prospective sparse sampling. Configured sizes are separate from
hypothetical retention fractions; count-phase timings exclude key hashing,
while complete consumer timings include it. No application data is captured.
