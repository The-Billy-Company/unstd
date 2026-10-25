Audio adds `wavx.decode_pcm` for interleaved little-endian PCM voice frames,
with owned writable NumPy output or dependency-free PCM16 lists. Floating
NumPy decoding converts directly and scales in place to avoid temporary arrays.
`wrap_pcm` accepts contiguous buffers, including bytearrays and typed memoryviews,
so accumulated narration can be wrapped without an intermediate bytes copy.
