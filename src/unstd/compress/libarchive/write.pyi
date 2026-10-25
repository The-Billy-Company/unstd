"""Typed libarchive-c 5.3 interface facts for the native write boundary."""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any, TypedDict, Unpack

from .entry import _Timestamp
from .ffi import _ArchivePointer

class _FileAttributes(TypedDict, total=False):
    mtime: _Timestamp
    ctime: _Timestamp
    birthtime: _Timestamp

class ArchiveWrite:
    _pointer: _ArchivePointer
    header_codec: str
    def __init__(
        self, archive_p: _ArchivePointer, header_codec: str = "utf-8"
    ) -> None: ...
    def __getattr__(self, name: str) -> Any: ...
    def add_file_from_memory(
        self,
        entry_path: str | bytes,
        entry_size: int,
        entry_data: bytes | Iterable[bytes],
        filetype: int = ...,
        permission: int = 0o664,
        **other_attributes: Unpack[_FileAttributes],
    ) -> None: ...

def file_writer(
    filepath: str,
    format_name: str,
    filter_name: str | None = None,
    archive_write_class: type[ArchiveWrite] = ...,
    options: str = "",
    passphrase: str | bytes | None = None,
    header_codec: str = "utf-8",
) -> AbstractContextManager[ArchiveWrite]: ...
def __getattr__(name: str) -> Any: ...
