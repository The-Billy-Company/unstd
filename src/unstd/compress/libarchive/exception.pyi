"""Typed libarchive-c 5.3 interface facts for the native exception boundary."""

from .ffi import _ArchivePointer

class ArchiveError(Exception):
    msg: str | bytes | None
    errno: int | None
    retcode: int | None
    archive_p: _ArchivePointer | None
    def __init__(
        self,
        msg: str | bytes | None,
        errno: int | None = None,
        retcode: int | None = None,
        archive_p: _ArchivePointer | None = None,
    ) -> None: ...
