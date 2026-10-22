"""One-shot I/O interruptions against real in-memory stream state.

Faults happen before the selected operation mutates the real backing stream.
They never patch a serializer or compression implementation. The trace records
the operations actually reached, rather than treating an armed fault as proof
that it ran.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
from typing import Literal


type Operation = Literal["read", "readline", "write", "flush", "seek", "close"]


@dataclass
class Fault:
    """A relative invocation and the exact exception supplied by the caller."""

    operation: Operation
    at: int
    error: BaseException
    fired: bool = False


class Schedule:
    """A bounded trace with at most one armed fault per stream experiment."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.events: list[dict[str, object]] = []
        self.fault: Fault | None = None
        self.trace_sha256 = hashlib.sha256()
        self.event_count = 0

    def arm(self, operation: Operation, error: BaseException, *, at: int = 1) -> Fault:
        if at < 1 or (self.fault is not None and not self.fault.fired):
            msg = "fault must have a positive invocation and no unspent predecessor"
            raise ValueError(msg)
        fault = Fault(operation, self.counts[operation] + at, error)
        self.fault = fault
        return fault

    def before(self, operation: Operation, *, size: int | None = None) -> None:
        self.counts[operation] += 1
        event: dict[str, object] = {
            "operation": operation,
            "invocation": self.counts[operation],
        }
        if size is not None:
            event["size"] = size
        fault = self.fault
        if (
            fault is not None
            and not fault.fired
            and operation == fault.operation
            and self.counts[operation] == fault.at
        ):
            fault.fired = True
            event["fault"] = type(fault.error).__name__
            self._record(event)
            raise fault.error
        self._record(event)

    def _record(self, event: dict[str, object]) -> None:
        self.event_count += 1
        self.trace_sha256.update(json.dumps(event, sort_keys=True).encode())
        self.trace_sha256.update(b"\n")
        self.events.append(event)
        if len(self.events) > 128:
            del self.events[64]

    def evidence(self) -> dict[str, object]:
        fault = self.fault
        return {
            "version": 1,
            "fault": (
                None
                if fault is None
                else {
                    "operation": fault.operation,
                    "at": fault.at,
                    "error": type(fault.error).__name__,
                    "fired": fault.fired,
                }
            ),
            "events": self.events.copy(),
            "counts": dict(self.counts),
            "event_count": self.event_count,
            "trace_sha256": self.trace_sha256.hexdigest(),
            "trace_truncated": self.event_count > len(self.events),
        }


class BytesStream(io.BytesIO):
    """A real BytesIO with observable operations and optional bounded transfers."""

    def __init__(
        self,
        data: bytes = b"",
        *,
        read_limit: int | None = None,
        write_limit: int | None = None,
    ) -> None:
        super().__init__(data)
        self.schedule = Schedule()
        self.read_limit = read_limit
        self.write_limit = write_limit

    def read(self, size: int = -1) -> bytes:
        self.schedule.before("read", size=size)
        if self.read_limit is not None:
            size = self.read_limit if size < 0 else min(size, self.read_limit)
        return super().read(size)

    def readline(self, size: int = -1) -> bytes:
        self.schedule.before("readline", size=size)
        return super().readline(size)

    def __next__(self) -> bytes:
        # BytesIO's C iterator bypasses overridden readline() by default.
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def write(self, data: bytes, /) -> int:
        self.schedule.before("write", size=len(data))
        if self.write_limit is not None:
            data = data[: self.write_limit]
        return super().write(data)

    def flush(self) -> None:
        self.schedule.before("flush")
        super().flush()

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.schedule.before("seek", size=offset)
        return super().seek(offset, whence)

    def close(self) -> None:
        self.schedule.before("close")
        super().close()


class TextStream(io.StringIO):
    """A TextIOBase instance so serializers exercise their real text branch."""

    def __init__(self, data: str = "") -> None:
        super().__init__(data)
        self.schedule = Schedule()

    def read(self, size: int = -1) -> str:
        self.schedule.before("read", size=size)
        return super().read(size)

    def readline(self, size: int = -1) -> str:
        self.schedule.before("readline", size=size)
        return super().readline(size)

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def write(self, data: str, /) -> int:
        self.schedule.before("write", size=len(data))
        return super().write(data)

    def flush(self) -> None:
        self.schedule.before("flush")
        super().flush()

    def close(self) -> None:
        self.schedule.before("close")
        super().close()
