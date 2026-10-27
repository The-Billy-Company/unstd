# Persistent result caching

Added `unstd.cache.Cache`, a zero-dependency persistent cache for expensive JSON
results shared by local workers. It composes the existing `serde.jsonx` codecs
with SQLite WAL, namespaces, optional expiration, and optional FIFO entry bounds.
`get_or_compute` coordinates producers through bounded key locks and short
database transactions. Independent keys can fill concurrently while same-key
callers share a result. Mutations and clearing fence in-flight producers; process
termination releases locks. Producer failures publish nothing; crashes and
expiry can cause recomputation. Reads return independent JSON values.

`get_many` reads a complete batch from one consistent snapshot; `set_many`
prepares and publishes an entire mapping atomically with one commit and shared
expiry. Empty batches avoid I/O. Batch operations amortize connection and commit
cost across evaluation summaries and other small results.

The new package includes public API contracts, persistence and contention tests,
and output-checked operation benchmarks. Existing performance ceilings remain
unchanged; new cache measurements retain their actual capture environment.

Typed artifact codecs let deep modules retain validated binary results without
intermediate JSON conversion. Cold and warm results both pass through codec
validation. Cache handles prepare canonical lock identities once; native Linux
and macOS qualification includes real preprocessing callers and retained paired
measurements, with cold-path costs and deployment limits documented.
