"""Audio I/O — numpy-native WAV read/write for voice paths.

A stdlib-``wave``-faithful *replacement* that hands back sample **arrays** instead
of raw frame bytes. The stdlib ``wave`` module only exposes packed PCM byte
strings; every TTS/STT call site then re-rolls the same ``struct``/``array``
unpacking. ``unstd.audio`` folds that decode into the reader and the encode into
the writer, so the surface is samples-in / samples-out.

Backend & fallback: the ``audio`` extra provides ``soundfile``
(libsndfile bindings) + ``numpy``. When present, reads/writes ride libsndfile and
samples are ``numpy`` arrays. Without the extra (a base ``unstd`` install) the
module falls back to the stdlib ``wave`` module plus manual PCM↔numpy conversion,
or — when ``numpy`` is also absent — the stdlib ``array`` module (reduced
ergonomics: 16-bit PCM only, Python ``list`` samples). The public surface works
everywhere; only the codec and the sample container differ.

Import the module, not its members::

    from unstd.audio import wavx
"""

from __future__ import annotations
