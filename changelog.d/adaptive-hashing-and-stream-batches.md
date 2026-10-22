One-shot BLAKE3 hashes, MACs, and key derivation now use four fresh workers from
1 MiB and eight from 4 MiB, capped by CPU count. An optional C helper batches
small immutable chunks from materialized stream lists and tuples into bounded
64 KiB updates for the existing BLAKE3 backend. Generators remain eager, mutable
buffers retain their backend behavior, and compiler-free installs keep the
Python path. Public APIs, digest values, validation, fork safety, and worker
refusal fallback are preserved. Paired measurements record medium-input and
small-chunk gains alongside overhead and scheduling noise.
