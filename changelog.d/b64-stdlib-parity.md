Base64 now exposes stdlib-compatible `b64encode`/`b64decode`, standard and
URL-safe functions, MIME byte helpers and `encode_file`/`decode_file` stream
helpers alongside the existing convenience API. Standard defaults, alternative
alphabets, buffer handling, exception types and diagnostics follow the running
stdlib. Acceleration is limited to plain-byte encoding and strict ASCII
decoding; other cases and native decode failures use stdlib.

The URL convenience decoder now behaves the same with and without pybase64:
ignored characters no longer interfere with missing-padding repair, completed
padding preserves stdlib's handling of trailing data, non-ASCII text raises
stdlib's `ValueError`, and strided decoding buffers work. URL decoding no longer
emits pybase64's alternate-alphabet warnings. Strict validation still accepts
nonzero unused pad bits, as stdlib does; it does not promise canonical spelling.
Independent stdlib differential tests and generated malformed-input contracts
cover the native backend and fallback. The decode benchmark now compares the
same strict validation mode on both paths.
