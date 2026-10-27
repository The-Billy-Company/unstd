Add `jsonx.dump_array` to write generators as compact JSON arrays with bounded
buffering, accelerated per-record encoding, and batched text or binary writes.
Supports gzip streams, short writes, and the dependency-free stdlib fallback;
returns the record count and leaves the caller's stream open.
