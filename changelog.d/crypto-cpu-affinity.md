Large one-shot BLAKE3 hashes, MACs, and key derivation now honor process CPU
affinity on Python 3.12 where the operating system exposes it. CPU-constrained
Linux workers avoid creating hashing pools sized for the whole host. Python
3.13+ retains its process CPU count and CPU-count override; platforms without
affinity support retain the host-count fallback. Digest values and public APIs
are unchanged.
