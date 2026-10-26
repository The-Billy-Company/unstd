# Numeric frame validation

`src/adapter.c` builds the optional `unstd.pack._pack_native` extension. It
validates an immutable byte frame containing integer row boundaries followed by
a homogeneous numeric matrix. Column counts, dtypes, byte order, finiteness, and
whether equal adjacent boundaries are permitted come from the generic caller.
No application dimensions, model identity, or publication policy live here.

Index decoding uses unsigned byte loads, including unaligned and foreign-order
fields. Float16/32/64 finiteness uses exponent bits, preserving signaling-NaN
rejection without floating-point arithmetic. AArch64 NEON and native-order SSE2
accelerate float32/64 scans; other cases retain the scalar classifier.
SIMD loops classify 64-byte groups with four independent reductions; the hot
NEON loop selects byte order before scanning the group.
For NEON float32 chunks of at least 64 KiB, an unsigned maximum reduction of
sign-cleared bit patterns admits only magnitudes below infinity. This removes
an inner-loop operation while still rejecting every infinity and NaN encoding.
Smaller chunks retain the exponent classifier. `KERNEL_VERSION = 2` identifies
this mechanism; the private validation signature is unchanged.

Only exact immutable `bytes` are accepted. The caller must snapshot mutable
storage before validation, so another thread cannot change admitted values.
With finite validation enabled, every floating payload is scanned on each
call; there is no validity cache. Large scans release the GIL in bounded chunks
and check signals after reacquiring it. No buffer export or input reference is
retained after return.

Public array construction and the fallback implementation belong to the Python
pack package. The native kernel returns only the admitted row count.

Rebuild the editable package before exercising changed C sources:

```console
uv sync --locked --extra pack --group dev --reinstall-package unstd
uv run --no-sync python -m pytest tests/suites/product/pack -q
```

The [release guide](../../tests/tooling/release/README.md) covers required native
wheel imports, installed contracts, and sdist inputs. Performance evidence is
kept separately in the [complete-decoder report](../../bench/reports/pack/ragged/README.md).
