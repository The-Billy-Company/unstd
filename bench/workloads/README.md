# Latency workloads

`speed_cases_codecs.py`, `speed_cases_operations.py` and `speed_cases_runtime.py` own output-checked fixtures for [`../speeds.py`](../speeds.py). Their logical fingerprint keys remain stable when files move; changes to fixture bytes still require review.

[`billy_gzip.json.gz`](billy_gzip.json.gz) freezes 14 synthetic transport shapes for
[`../profiles/billy_gzip.py`](../profiles/billy_gzip.py): complete ijson dataset
queries, uploads read twice in 256 KiB chunks with position restoration, bounded
HTTP reads, and journal writes, batch flushes and replay across one or two gzip
members. It contains payloads and hashed consumer source receipts, with no Billy
source bodies or production data. CI consumes this committed file without a
Billy checkout. The gate checks every shape and independently verifies decoded
bytes before measuring either installation.

See the [benchmark guide](../README.md).

[`billy_gzip_requests.json.gz`](billy_gzip_requests.json.gz) separately freezes
eight public synthetic request sequences for
[`../profiles/billy_gzip_requests.py`](../profiles/billy_gzip_requests.py).
Content-addressed 64 KiB chunks keep shared prefixes compact; every chunk and
reassembled body has an independent SHA256 check. The source receipts cover
Billy's direct encoder, request size/shrink gate and immutable compressor
checkpoint. No production payloads or Billy source bodies are included.
