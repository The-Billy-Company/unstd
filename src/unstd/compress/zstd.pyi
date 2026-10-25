"""The running interpreter's stdlib Zstandard typing surface."""

import sys

if sys.version_info >= (3, 14):
    from compression.zstd import *  # noqa: F403 — preserve the complete stdlib API.
