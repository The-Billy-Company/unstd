"""PCM buffers and sample-oriented WAV I/O for voice paths.

``wavx.wrap_pcm`` adds a WAV container to wire PCM without decoding it;
``wavx.decode_pcm`` normalizes raw frames without constructing a WAV file.
``read``/``write`` and ``blocks``/``Writer`` handle complete or streamed WAVs
with decoded sample arrays instead of stdlib ``wave``'s packed frame bytes.

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
