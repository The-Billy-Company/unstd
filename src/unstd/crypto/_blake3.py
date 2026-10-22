"""The BLAKE3 backend, imported once for :mod:`digest` and :mod:`token`.

BLAKE3 has **no stdlib equivalent** — ``hashlib`` ships BLAKE2b, a *different*
algorithm, so a silent fallback would fork the digest rather than slow it down.
The guard lives here, not in the package ``__init__``, so the pure-stdlib
:mod:`unstd.crypto.tls` still imports on a base install.
"""

from __future__ import annotations

import os
from typing import TypedDict, Unpack


try:
    from blake3 import blake3
except ImportError as exc:  # fail loud, don't fork the digest
    msg = "unstd.crypto.digest/token require the 'crypto' extra — install with: pip install 'unstd[crypto]'"
    raise ImportError(msg) from exc


# Amortize pool creation only on substantial inputs. A positive count creates a
# fresh, bounded pool for each hasher; AUTO uses Rayon's process-global pool,
# which can deadlock after fork. Never cache keyed hashers or their secret keys.
_PARALLEL_MIN_BYTES = 16 * 1024 * 1024
_PARALLEL_THREADS = min(8, os.cpu_count() or 1)


class _HashOptions(TypedDict, total=False):
    """The two independent BLAKE3 modes accepted by the internal constructor."""

    key: bytes
    derive_key_context: str


def _parallel_hasher(data: bytes, **options: Unpack[_HashOptions]) -> blake3:
    """A fresh worker pool, with serial fallback if the OS refuses its threads."""
    try:
        return blake3(data, max_threads=_PARALLEL_THREADS, **options)
    except BaseException as error:
        # blake3-py's pool builder unwraps its Result. PyO3 exposes a failed
        # allocation as a BaseException rather than an Exception. Catch only
        # that pool failure; interrupts, validation errors and other panics keep
        # their original behavior.
        kind = type(error)
        if (
            kind.__module__ != "pyo3_runtime"
            or kind.__name__ != "PanicException"
            or "ThreadPoolBuildError" not in str(error)
        ):
            raise
    return blake3(data, **options)


__all__ = ["blake3"]
