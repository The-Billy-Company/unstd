Defer package metadata lookup until `unstd.__version__` is requested and cache
the result. Importing submodules such as `unstd.serde.structs` now avoids that
startup cost while retaining the same native codecs, parsing behavior, and
metadata-derived version (including the uninstalled-source fallback).
