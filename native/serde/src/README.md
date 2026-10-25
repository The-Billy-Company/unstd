# JSON kernel source

[`_json_native.c`](_json_native.c) owns the CPython module, runtime guards, and
codec bindings. Its adjacent headers separate
[encoding](_json_encode_native.h), [decoding](_json_decode_native.h), and
[line-oriented decoding](_json_lines_native.h). They form one extension and
retain their local include paths. Build output remains
`unstd.serde._json_native`.
