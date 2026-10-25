"""The running interpreter's original zlib module, loaded on demand."""

import sys
import zlib as _backend


sys.modules[__name__] = _backend
