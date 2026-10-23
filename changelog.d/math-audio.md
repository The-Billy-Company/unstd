Read PCM16 as `int16` through the NumPy fallback by copying sample counts
directly, avoiding float normalization and requantization. Whole-file and
streaming reads retain owned writable arrays, little-endian dtype, channel
shape and frame validation. SoundFile routing and float conversions are unchanged.

Added authenticated patched-libsndfile build tooling, host-qualified SoundFile
wheels and offline admission of the actual loaded library against separately
trusted provenance. A required audio-security CI lane builds, admits and tests
the patched reference artifact. These checks run outside production audio calls.
