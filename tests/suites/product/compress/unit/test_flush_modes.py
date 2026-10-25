"""Invalid gzip flush modes preserve stream state and callback ordering."""

from __future__ import annotations

import gzip
import io
import zlib

import pytest

from unstd.compress import gzipx


@pytest.mark.parametrize("mode", [2**31, 2**32 + 4, 2**40, -(2**31) - 1, -(2**40)])
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("codec", [gzip, gzipx])
def test_oversized_modes_cannot_finish_a_writer(mode, indexed, codec):
    target = io.BytesIO()
    calls = []

    class Mode:
        def __index__(self):
            calls.append("index")
            return mode

    with codec.GzipFile(fileobj=target, mode="wb", mtime=0) as writer:
        writer.write(b"prefix")
        with pytest.raises(OverflowError):
            writer.flush(Mode() if indexed else mode)
        writer.write(b"suffix")
    assert gzip.decompress(target.getvalue()) == b"prefixsuffix"
    assert calls == (["index"] if indexed else [])


def test_mode_coercion_stays_after_pending_writes_and_inside_native_flush():
    class CloseDuringIndex:
        def __init__(self, writer, events):
            self.writer = writer
            self.events = events

        def __index__(self):
            self.events.append(self.writer.size)
            self.writer.close()
            return zlib.Z_SYNC_FLUSH

    observed = []
    for codec in (gzip, gzipx):
        target = io.BytesIO()
        writer = codec.GzipFile(fileobj=target, mode="wb", mtime=0)
        writer.write(b"pending prefix")
        events = []

        with pytest.raises(zlib.error):
            writer.flush(CloseDuringIndex(writer, events))
        assert writer.closed
        assert not target.closed
        observed.append((events, gzip.decompress(target.getvalue())))
    assert observed == [([14], b"pending prefix")] * 2


def test_readers_and_closed_writers_do_not_coerce_unused_modes():
    class NeverIndex:
        def __index__(self):
            message = "unused flush argument was coerced"
            raise AssertionError(message)

    packed = gzip.compress(b"source", mtime=0)
    for codec in (gzip, gzipx):
        with codec.GzipFile(fileobj=io.BytesIO(packed), mode="rb") as reader:
            assert reader.flush(NeverIndex()) is None
        with pytest.raises(ValueError, match="closed"):
            reader.flush(NeverIndex())
        target = io.BytesIO()
        writer = codec.GzipFile(fileobj=target, mode="wb", mtime=0)
        writer.close()
        with pytest.raises(ValueError, match="closed"):
            writer.flush(NeverIndex())
