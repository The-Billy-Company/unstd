"""Typed libarchive-c 5.3 interface facts for the native read boundary."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from .entry import ArchiveEntry
from .ffi import _ArchivePointer

class ArchiveRead:
    _pointer: _ArchivePointer
    header_codec: str
    def __init__(
        self, archive_p: _ArchivePointer, header_codec: str = "utf-8"
    ) -> None: ...
    def __iter__(self) -> Iterator[ArchiveEntry]: ...
    @property
    def bytes_read(self) -> int: ...
    @property
    def filter_names(self) -> list[bytes | None]: ...
    @property
    def format_name(self) -> bytes | None: ...

def new_archive_read(
    format_name: str = "all",
    filter_name: str = "all",
    passphrase: str | bytes | None = None,
) -> AbstractContextManager[int]: ...
def __getattr__(name: str) -> Any: ...
