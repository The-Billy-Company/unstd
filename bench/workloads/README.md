# Latency workloads

`speed_cases_codecs.py`, `speed_cases_operations.py` and `speed_cases_runtime.py` own output-checked fixtures for [`../speeds.py`](../speeds.py). Their logical fingerprint keys remain stable when files move; changes to fixture bytes still require review.

See the [benchmark guide](../README.md).

[`billy_fs.json`](billy_fs.json) freezes 13 synthetic filesystem scenarios from
captured consumer contracts: token/config/report writes, browser state writes
and complete cache-budget scans, and notebook checkpoints. Receipts retain
consumer file/symbol hashes and the config/report input hashes. Payloads contain
no captured tokens, cookies, notebooks or login state. Sizes and peer counts are
sensitivity scenarios without production-frequency weights.

Both the [historical experiment driver](../profiles/billy_fs.py) and the
[installed public-API driver](../profiles/billy_fs_installed.py) verify those
receipts and exact encoded payload hashes before and after execution. They need
the matching Billy source checkout to compile checked browser/notebook reference
ASTs; application dependencies are not imported. A changed source or input fails
verification. Deliberately recapture with `billy_fs.py fixtures --billy-source
PATH --output PATH` when reviewing a new consumer snapshot.

The complete browser cases keep encoding and quota scanning. Report cases use
shared stdlib JSON, excluding actual `jsonx` codec latency. Browser/notebook
reference writers reset modes while fs candidates preserve them; fixtures start
at 0600. Strict notebook candidates add preflight directory syncing. Those are
prospective migration boundaries, not proof of drop-in application parity.
The [production qualification](../reports/fs/production/README.md) documents
installed routing checks, retained results and platform/storage limitations;
the [earlier evaluation](../reports/fs/billy-linux.md) retains prototype results.

The rand receipts in [`billy_rand.json`](billy_rand.json),
[`rand_keyed.json`](rand_keyed.json), and
[`rand_strategies.json`](rand_strategies.json) retain checked consumer source,
AST/configuration hashes and synthetic input sizes. The drivers distinguish
unchanged output/state comparisons, explicitly different SplitMix64 count
batches, and prospective sparse sampling. Configured sizes are separate from
hypothetical retention fractions; count-phase timings exclude key hashing,
while complete consumer timings include it. No application data is captured.
