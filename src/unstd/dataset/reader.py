"""Read-only JSON sequences with explicit ordered selection and bounded batches."""

from __future__ import annotations

from collections.abc import (
    Iterable,
    Iterator,
    Sequence,
)
import operator
from os import PathLike  # noqa: TC003 — public annotation introspection
from types import TracebackType  # noqa: TC003 — public annotation introspection
from typing import Self, cast, overload, override

from unstd.dataset.storage import Store
from unstd.serde import jsonx, ndjson
from unstd.serde.jsonx import Json


__all__ = []


class Dataset(Sequence[Json]):
    """An immutable, mapped sequence of JSON values with indexed row access.

    Use a context manager or call ``close()``. Every read returns independent
    values. Slices and ``take`` return lists; iteration and ``iter_rows`` decode
    one row at a time. ``batches`` explicitly decodes a bounded batch at a time.
    Read-only operations can share a handle across threads; callers coordinate
    closing with active readers. Files must only change by atomic replacement.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        """Open an existing indexed snapshot without reading its row payloads."""
        self._store = Store(path)

    def _position(self, index: int) -> int:
        """A normalized valid row index, following Python sequence semantics."""
        position = operator.index(index)
        if position < 0:
            position += self._store.count
        if not 0 <= position < self._store.count:
            msg = "dataset index out of range"
            raise IndexError(msg)
        return position

    @override
    def __len__(self) -> int:
        """The persisted row count; no records are decoded."""
        self._store.opened()
        return self._store.count

    @overload
    def __getitem__(self, index: int) -> Json: ...

    @overload
    def __getitem__(self, index: slice) -> list[Json]: ...

    @override
    def __getitem__(self, index: int | slice) -> Json | list[Json]:
        """An independent JSON row or a list following ordinary slice semantics."""
        self._store.opened()
        if isinstance(index, slice):
            start, stop, step = index.indices(self._store.count)
            return self.take(range(start, stop, step))
        return cast("Json", jsonx.loads(self._store.row(self._position(index))))

    @override
    def __iter__(self) -> Iterator[Json]:
        """Decode one row at a time with an independent iterator position."""
        return self.iter_rows()

    def take(self, indices: Iterable[int]) -> list[Json]:
        """Select rows in request order, preserving duplicates and negative indices.

        All indices are validated before any JSON is decoded. This operation
        batches offset extraction without sorting or deduplicating the request.
        The returned list materializes exactly the caller's selection.
        """
        self._store.opened()
        positions = tuple(self._position(index) for index in indices)
        return [jsonx.loads(raw) for raw in self._store.take(positions)]

    def iter_rows(
        self, start: int | None = None, stop: int | None = None, step: int = 1
    ) -> Iterator[Json]:
        """Lazily decode a slice, including negative bounds and reverse steps.

        Bounds follow ``sequence[start:stop:step]``. Validation and reads begin
        on first iteration. Nothing is decoded ahead of the requested next row.
        """
        self._store.opened()
        first, last, stride = slice(start, stop, step).indices(self._store.count)
        for position in range(first, last, stride):
            yield jsonx.loads(self._store.row(position))

    def batches(
        self, size: int = 1024, *, start: int | None = 0, stop: int | None = None
    ) -> Iterator[list[Json]]:
        """Stream bounded contiguous batches, with Python slice bounds.

        ``size`` is a positive integer, excluding booleans. Each yielded list
        holds at most that many independent rows. The last batch may be smaller.
        Reads and argument validation begin on first iteration. Closing the
        owner invalidates pending iterators; no exported mapping views remain.
        """
        self._store.opened()
        if type(size) is not int or size <= 0:
            msg = "batch size must be a positive integer"
            raise ValueError(msg)
        first, last, _step = slice(start, stop).indices(self._store.count)
        for begin in range(first, last, size):
            end = min(begin + size, last)
            rows = ndjson.loads(self._store.span(begin, end))
            if len(rows) != end - begin:
                msg = "dataset batch row count mismatch"
                raise ValueError(msg)
            yield rows
            del rows

    def verify(self) -> None:
        """Check every offset and the body checksum without decoding records.

        Opening checks geometry and endpoints only. Call this linear operation
        when validating a transferred artifact. The checksum detects corruption;
        it provides no authenticity guarantee against a modified file and hash.
        """
        self._store.verify()

    def close(self) -> None:
        """Release the mapping; repeated calls are safe and closed reads fail."""
        self._store.close()

    def __enter__(self) -> Self:
        """The open dataset owned by this context."""
        self._store.opened()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the snapshot on success, failure, or interruption."""
        self.close()
