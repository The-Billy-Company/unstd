"""The original optional native Zstandard backend, without call adapters."""

import sys

from unstd.compress._ports import register_package as _register_package


try:
    import zstandard as _backend
except ImportError as error:
    _message = (
        "unstd.compress.zstandard requires the 'zstd' extra - pip install 'unstd[zstd]'"
    )
    raise ImportError(_message) from error

_register_package(__name__, _backend)
sys.modules[__name__] = _backend
