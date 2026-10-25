# Authenticated gzip dependencies

[`libdeflate/`](libdeflate/) contains the libdeflate 1.25 gzip decoding, CRC32 and
CPU-dispatch closure ([MIT license](libdeflate/COPYING),
[manifest](libdeflate/UPSTREAM.json)). [`zlib/`](zlib/) contains the zlib 1.3.2
inflation closure ([license](zlib/LICENSE), [manifest](zlib/UPSTREAM.json)); macOS
uses its headers but links platform `libz`, while other platforms compile it with
`Z_PREFIX` and checked Python RAW allocators.

Upstream files are unmodified. Each manifest records the archive URL, SHA256 and
every retained file hash. [Supply-chain policy](../../../tests/tooling/supply_chain/policy.py)
and [release verification](../../../tests/tooling/release/verify.py) check the
closure; both licenses ship in distributions. Project-owned changes belong in
[`../src/`](../src/README.md).
