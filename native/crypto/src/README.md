# Stream and token adapter

[`adapter.c`](adapter.c) builds `unstd.crypto._stream_native` and supplies two
private entrypoints:

| Entry point | Responsibility |
| --- | --- |
| `absorb(hasher, chunks)` | Coalesce exact `bytes` chunks of at most 2 KiB from exact lists/tuples into a 64 KiB scratch buffer. Flush pending bytes before passing other chunks unchanged to `hasher.update`. |
| `bind_token_verifier(fallback)` | Bind the Python fallback and its globals; the resulting callable checks exact-string envelope size, ASCII, separator and canonical base64url before decoding or MAC work. String subclasses use the fallback. |

Batching uses real iterators and owned chunk references across mutation,
callbacks and backend GIL releases. Generic iterables return `NotImplemented`
so the Python constructor retains their eager update loop. Pending batches
flush before signal callbacks; allocation, callback and signal errors propagate.
Scratch, iterators and the bound update method are released on return.

The token helper rereads the current Python decoder, MAC and comparison
functions at their expression boundaries, preserving replacements made by
callbacks. It authenticates the encoded payload before decoding payload bytes.
Its persistent context holds the fallback, globals and static names, without
storing request keys, token arguments or hashers. Keep cryptographic operations
in those Python/backend functions when extending the parser.
