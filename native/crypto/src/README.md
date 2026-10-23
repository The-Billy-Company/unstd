# Hash-stream kernel source

[`_stream_native.c`](_stream_native.c) batches bounded stream absorption while
retaining Python error and cleanup behavior. The build hook compiles it into
`unstd.crypto._stream_native`; it does not introduce a separate user API.
