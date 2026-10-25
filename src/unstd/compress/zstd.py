"""The original stdlib Zstandard module on Python 3.14 and later."""

import sys

from unstd.compress.engines.imports import register_package as _register_package


if sys.version_info >= (3, 14):
    from compression import zstd as _backend

    _register_package(__name__, _backend)
    sys.modules[__name__] = _backend
else:
    _message = "unstd.compress.zstd requires Python 3.14 or later; the native zstandard API is available through 'unstd[zstd]'"
    raise ImportError(_message)
