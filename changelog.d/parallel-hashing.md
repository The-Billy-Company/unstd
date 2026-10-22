Large one-shot BLAKE3 hashes, MACs, and key derivation now use up to eight
workers for exact `bytes` inputs of at least 16 MiB. Fresh worker pools remain
usable after a fork and fall back to serial hashing if the backend cannot
create its threads. Public APIs, digest values, validation, accepted buffers,
legacy BLAKE2b identities, and incremental streaming behavior are unchanged.
