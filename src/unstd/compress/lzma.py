"""The running interpreter's original lzma module, loaded on demand."""

import lzma as _backend
import sys


sys.modules[__name__] = _backend
