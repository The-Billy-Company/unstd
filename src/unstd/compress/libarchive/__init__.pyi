"""The original libarchive-c API with typed native reader/writer boundaries."""

from typing import Any

from . import (
    entry as entry,
)
from . import (
    exception as exception,
)
from . import (
    ffi as ffi,
)
from . import (
    read as read,
)
from . import (
    write as write,
)
from .entry import ArchiveEntry as ArchiveEntry
from .exception import ArchiveError as ArchiveError
from .extract import extract_fd as extract_fd
from .extract import extract_file as extract_file
from .extract import extract_memory as extract_memory
from .read import custom_reader as custom_reader
from .read import fd_reader as fd_reader
from .read import file_reader as file_reader
from .read import memory_reader as memory_reader
from .read import seekable_stream_reader as seekable_stream_reader
from .read import stream_reader as stream_reader
from .write import custom_writer as custom_writer
from .write import fd_writer as fd_writer
from .write import file_writer as file_writer
from .write import memory_writer as memory_writer

def __getattr__(name: str) -> Any: ...
