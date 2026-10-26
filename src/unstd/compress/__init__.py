"""Compression codecs with optional native backends and stdlib fallbacks.

``gzipx`` preserves the stdlib gzip file surface with native compression,
bounded inflation and CRC behind the ``compress`` extra. The interpreter's gzip
framing and buffering remain in use; base installs bind directly to stdlib.

``zstandard`` is the optional original native backend. ``zstd`` is Python
3.14's stdlib API; ``bz2``, ``lzma``, ``zlib``, ``tarfile`` and ``zipfile`` are
the running interpreter's modules. These namespaces retain original classes,
functions, exceptions and ownership semantics without per-call adapters.
"""

from __future__ import annotations

from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING


if _TYPE_CHECKING:
    import sys
    from types import ModuleType

    from unstd.compress import bz2 as bz2
    from unstd.compress import libarchive as libarchive
    from unstd.compress import lzma as lzma
    from unstd.compress import tarfile as tarfile
    from unstd.compress import zipfile as zipfile
    from unstd.compress import zlib as zlib
    from unstd.compress import zstandard as zstandard

    if sys.version_info >= (3, 14):
        from unstd.compress import zstd as zstd


_MODULES = frozenset(
    {"bz2", "libarchive", "lzma", "tarfile", "zipfile", "zlib", "zstandard", "zstd"}
)


def __getattr__(name: str) -> ModuleType:
    """Cache the original provider only when its namespace is requested."""
    if name not in _MODULES:
        message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(message)
    backend = _import_module(f"{__name__}.{name}")
    globals()[name] = backend
    return backend


def __dir__() -> list[str]:
    """Expose namespaces without loading an optional codec or archive engine."""
    return sorted({*globals(), *_MODULES, "gzipx", "zlibx"})
