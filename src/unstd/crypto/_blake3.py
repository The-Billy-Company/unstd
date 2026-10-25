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
_PARALLEL_MIN_BYTES = 1024 * 1024


def _available_cpus() -> int:
    """The process CPU count, honoring affinity on Python 3.12 where available."""
    process_count = getattr(os, "process_cpu_count", None)
    if process_count is not None:
        count: int | None = process_count()
        return count or 1
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return len(affinity(0)) or 1
        except OSError:
            pass
    return os.cpu_count() or 1


# Resolve once at import. Python 3.13+ also honors its CPU-count override;
# older interpreters use process affinity before falling back to the host.
_PARALLEL_THREADS = min(8, _available_cpus())


class _HashOptions(TypedDict, total=False):
    """The two independent BLAKE3 modes accepted by the internal constructor."""

    key: bytes
    derive_key_context: str


def _parallel_hasher(data: bytes, **options: Unpack[_HashOptions]) -> blake3:
    """A fresh worker pool, with serial fallback if the OS refuses its threads."""
    # Four workers amortize their startup from 1 MiB; the full pool pays off
    # from 4 MiB. Smaller inputs keep the serial path in the public wrappers.
    workers = min(_PARALLEL_THREADS, 4 if len(data) < 4 * 1024 * 1024 else 8)
    try:
        return blake3(data, max_threads=workers, **options)
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
