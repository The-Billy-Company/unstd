# Validation kernel

`adapter.c` owns argument validation, checked frame geometry, integer boundary
validation, float exponent scans, and CPython registration. Metadata is checked
before reading numeric storage. Scalar loads do not assume alignment or cast
out-of-range unsigned values into signed C integers.

SIMD is selected at compile time. Float32/64 NEON scans support both byte orders;
SSE2 handles native little-endian data and uses the scalar path for foreign
order. Integer dtypes are inherently finite. Float16 uses the scalar exponent
classifier. The module has no NumPy build or ABI dependency.
