# Validation kernel

`adapter.c` owns argument validation, checked frame geometry, integer boundary
validation, float exponent scans, and CPython registration. Metadata is checked
before reading numeric storage. Scalar loads do not assume alignment or cast
out-of-range unsigned values into signed C integers.

SIMD is selected at compile time. Float32/64 NEON scans support both byte orders;
SSE2 handles native little-endian data and uses the scalar path for foreign
order. Integer dtypes are inherently finite. Float16 uses the scalar exponent
classifier. The module has no NumPy build or ABI dependency.

NEON float32 chunks containing at least 16,384 values use four independent
unsigned maximum reductions after clearing each sign bit. Every finite
magnitude has a bit pattern below positive infinity; every NaN or infinity is
at or above it. Byte reversal precedes classification for foreign-order input.
The scalar tail and the existing bounded scan checkpoints remain in place.
`KERNEL_VERSION` is 2; the private function signature is unchanged.
