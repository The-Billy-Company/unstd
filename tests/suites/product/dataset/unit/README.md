# Dataset sequence laws

Independent list models cover indexing, ordered bulk selection, lazy strided
reads, and bounded batches. Decoder input budgets prove that unconsumed rows
are not read ahead. Configuration, JSON normalization, close behavior,
interleaved iterators, and shared read-only handles have explicit contracts.

Independent binary vectors verify the header, little-endian offset table,
record framing, and SHA256 digest. Selected corrupt reads and full verification
exercise different boundaries. Both portable and native implementations run
against the same value models. Hostile native geometry and buffer cleanup run
in a bounded worker; a blocked native import proves the fallback stays usable.

Ownership regressions retain real child memoryviews to prove that verification
releases exports explicitly, and inject a source-context close failure after
mapping to prove that the mapping closes while the original error survives.
Both controls run with portable and native ownership.

Runtime annotation checks keep the public interface usable by schema and
documentation tools. A larger streamed corpus crosses index and checksum I/O
chunks; [generated JSON laws](../properties/README.md) cover values across every
access pattern.
