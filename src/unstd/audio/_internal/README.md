# Private audio implementation

`pcm.py` owns scalar and frame validation, integer clipping, channel checks,
and stdlib PCM16 conversion. It has no runtime dependency on NumPy or soundfile.
The public `wavx` module owns WAV I/O, backend selection, public types,
streaming writers, and NumPy conversion paths.
