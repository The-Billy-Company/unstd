"""The running interpreter's original bz2 module, loaded on demand."""

import bz2 as _backend
import sys


sys.modules[__name__] = _backend
