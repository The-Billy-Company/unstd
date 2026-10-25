# Native JSON dispatch

[`src/`](src/README.md) builds the optional `unstd.serde._json_native` extension.
It requires the GIL and `orjson`; native decoding also requires `msgspec`.
[`jsonx`](../../src/unstd/serde/jsonx.py) and
[`ndjson`](../../src/unstd/serde/ndjson.py) own stdlib compatibility, options, and
fallback. Use the [public API](../../src/unstd/serde/README.md).
