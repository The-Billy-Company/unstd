# Latency workloads

`speed_cases_codecs.py`, `speed_cases_operations.py` and `speed_cases_runtime.py` own output-checked fixtures for [`../speeds.py`](../speeds.py). Their logical fingerprint keys remain stable when files move; changes to fixture bytes still require review.

The gzip consumer fixtures freeze 14 synthetic transport shapes: complete ijson
dataset queries, uploads read twice in 256 KiB chunks with position restoration,
bounded HTTP reads, and journal writes, batch flushes and replay across one or
two gzip members. They contain payloads and hashed consumer source receipts, with no
upstream source bodies or production data. CI consumes the committed fixtures
without an external checkout. The gate checks every shape and independently
verifies decoded bytes before measuring either installation.

See the [benchmark guide](../README.md).

We keep application-shaped clone records in [`clone_consumers.py`](clone_consumers.py).
The optional `clone_common_cpu --consumer-cases` profile checks small and nested
models, prepared images, slotted rows, shared state and copy-hook boundaries.

The process consumer receipts freeze 16 current process callsites across 14
source files, with hashes, excerpts and argument/I/O contracts. The process
consumer profile creates disposable Git repositories and synthetic text scaling
fixtures from those contracts. Its
stdin case is explicitly synthetic; existing binary asyncio/media paths are
recorded separately and retain their current APIs.

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

`billy_gzip_requests.json.gz` separately freezes 11 synthetic request and dataset
materialization cases for [`../profiles/billy_gzip_requests.py`](../profiles/billy_gzip_requests.py).
The `gzipx` profile selects six whole-buffer cases: three direct request encodes
and three dataset materializations. The remaining request sequences exercise
the existing `zlibx` compressor checkpoints. The original 14 gzip stream cases
remain unchanged. Content-addressed 64 KiB chunks keep shared prefixes compact;
every chunk and reassembled body has an independent SHA256 check. Source
receipts cover the direct encoder, request size/shrink gate, immutable compressor
checkpoint and dataset materialization. No production payloads or upstream
source bodies are included.

The rand receipts in [`billy_rand.json`](billy_rand.json),
[`rand_keyed.json`](rand_keyed.json), and
[`rand_strategies.json`](rand_strategies.json) retain checked consumer source,
AST/configuration hashes and synthetic input sizes. The drivers distinguish
unchanged output/state comparisons, explicitly different SplitMix64 count
batches, and prospective sparse sampling. Configured sizes are separate from
hypothetical retention fractions; count-phase timings exclude key hashing,
while complete consumer timings include it. No application data is captured.

Packing's [backend profile](../profiles/pack_backend.py) builds deterministic
typed arrays and buffer regions, checking exact wire bytes and selected
`count`/`offset` ranges. The [consumer profile](../profiles/pack_consumers.py)
retains checked consumer receipts and times complete decode/publication slices;
it labels NumPy views and owned-list outputs as different API choices.
