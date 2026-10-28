---
doc_radar:
  sentinels:
    - file: src/adapter.c
      contains: ['within(PyObject *module', 'PyBuffer_IsContiguous', 'PyErr_CheckSignals']
---

# Portable Math Kernels

The optional CPython extension specializes real ndarray operations through Python's
buffer protocol. It needs no NumPy headers, C ABI, BLAS library or ISA flags.
Native-endian float32/float64 data supports read-only and strided layouts; top-k
and labels also preserve native integer ordering. Unsupported inputs return
`NotImplemented` for the Python implementation to handle.

Small matrix kernels avoid Python dispatch and intermediate arrays. Larger row
reductions stay with NumPy; grouped means use a single native accumulation pass.
Top-k uses a stable heap for a small selected fraction and NumPy's stable sort
for dense selection. Framing builds checked ndarray views over a private read-only
buffer owner. That owner retains the source export and prevents callers from
re-enabling writes through an overlapping frame view.

Exact integer comparisons scan matching contiguous native-endian 64-bit arrays
with scalar integer tolerance. They allocate no working arrays, check signals
between bounded chunks and release their input exports on success or failure;
other dtypes, layouts and tolerances retain the portable implementation.

Kernel version 2 adds this comparison. The public wrapper checks that version
before calling it, so an older binary keeps the portable implementation.

Each interpreter owns its lazy NumPy callable/type cache in module state. Kernels
hold the GIL while reading buffers; no mutable process-global Python state or
background work is created. Output buffers are checked before writes, and loops
check for pending signals. The read-only frame owner releases its source export
when the last frame view goes away.

Fast-math is disabled. Numeric kernels preserve the calling thread's existing
floating-point flags and check newly raised overflow, underflow, invalid and
zero-division flags. Exceptional calculations decline to the NumPy implementation,
which owns warning, exception and callback policy. Top-k and framing perform no
floating-point reductions and do not need that guard.
