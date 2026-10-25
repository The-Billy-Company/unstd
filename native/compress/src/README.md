# Gzip adapter and inflater

[`adapter.c`](adapter.c) owns the private CPython module and libdeflate buffer
operations. [`stream.c`](stream.c) owns the bounded streaming inflater type;
[`stream.h`](stream.h) declares its shared boundary. [`inflate.c`](inflate.c)
bridges that boundary to platform zlib on macOS or the authenticated vendored
zlib closure elsewhere.

The build hook compiles these files into the existing
`unstd.compress._gzip_native` extension. Upstream code and licenses live under
[`../vendor/`](../vendor/README.md); optional reviewed candidates live under
[`../experiments/`](../experiments/README.md).
