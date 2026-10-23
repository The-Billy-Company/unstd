"""Compression codecs with optional native backends and stdlib fallbacks.

``gzipx`` preserves the stdlib gzip file surface with native compression,
bounded inflation and CRC behind the ``compress`` extra. The interpreter's gzip
framing and buffering remain in use; base installs bind directly to stdlib.
"""
