"""The running interpreter's original tarfile module, loaded on demand."""

import sys
import tarfile as _backend


sys.modules[__name__] = _backend
