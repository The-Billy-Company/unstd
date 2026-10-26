"""The original optional libarchive-c package and its native submodules."""

from importlib import import_module as _import_module
from pkgutil import iter_modules as _iter_modules
import sys as _sys


try:
    _backend = _import_module("libarchive")
    # Register original provider modules before exposing its package __path__.
    # A qualified import must never execute ctypes bindings under a new name.
    _aliases = {
        f"{__name__}.{_module_info.name}": _import_module(
            f"libarchive.{_module_info.name}"
        )
        for _module_info in _iter_modules(_backend.__path__)
    }
except (ImportError, OSError, AttributeError) as _error:
    _message = "unstd.compress.libarchive requires the 'archives' extra and a loadable native libarchive library - pip install 'unstd[archives]' and provide libarchive"
    raise ImportError(_message) from _error

_sys.modules.update(_aliases)
_sys.modules[__name__] = _backend
