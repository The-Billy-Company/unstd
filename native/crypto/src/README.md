# Stream and token helpers

[`_stream_native.c`](_stream_native.c) supplies two private helpers:

- `absorb` accepts exact lists/tuples and coalesces exact `bytes` chunks of at
  most 2 KiB into a 64 KiB scratch buffer. Other chunks reach `hasher.update`
  unchanged after pending bytes flush. Real iterators and owned chunk references
  survive mutation, callbacks and backend GIL releases. Generic iterables return
  `NotImplemented`; the Python constructor keeps their eager update loop.
- `bind_token_verifier` binds the Python fallback and its globals. Exact strings
  get size, ASCII, separator and canonical base64url checks before decoding or
  MAC work; subclasses use the fallback. The helper calls the current Python
  decoder, MAC and comparison functions, and decodes claims only after a match.

The extension retains no stream scratch or request references after return.
A bound verifier retains its fallback, globals and static names, without caching
request keys, tokens or hashers. Allocation, callback and signal errors propagate
to Python.
