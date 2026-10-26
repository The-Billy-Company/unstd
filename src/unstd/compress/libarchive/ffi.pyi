"""Typed libarchive-c 5.3 interface facts for the native ffi boundary."""

from collections.abc import Callable
from ctypes import c_void_p
from typing import Any

type _ArchivePointer = int | c_void_p

error_string: Callable[[_ArchivePointer], bytes | None]

def get_read_format_function(format_name: str) -> Callable[[_ArchivePointer], int]: ...

read_open_memory: Callable[[_ArchivePointer, bytes | c_void_p, int], int]

version_number: Callable[[], int]
libarchive_path: str | None

def __getattr__(name: str) -> Any: ...
