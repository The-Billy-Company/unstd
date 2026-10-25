"""The running interpreter's original zipfile module, loaded on demand."""

import sys
import zipfile as _backend

from unstd.compress._ports import register_package as _register_package


_register_package(__name__, _backend)
sys.modules[__name__] = _backend
