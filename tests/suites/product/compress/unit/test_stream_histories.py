"""Short sources, later members and incomplete bodies retain gzip read histories."""

from __future__ import annotations

import gzip
import io
from random import Random
import struct
import zlib

import pytest

from unstd.compress import gzipx


def _member(kind):
    rng = Random(59231)  # noqa: S311 — reproducible codec fixture
    raw = rng.randbytes(24576).translate(bytes(i & 31 for i in range(256)))
    encoder = zlib.compressobj(wbits=-15)
    prefix = encoder.compress(raw) + encoder.flush(zlib.Z_SYNC_FLUSH)
    header = b"\x1f\x8b\x08\x00" + bytes(6)
    if kind == "truncated":
        return header + prefix[:-1]
    if kind == "truncated-header":
        return header + prefix + b"\x01\x00\x00\xff"
    if kind == "bad-block":
        return header + prefix + b"\x07"
    body = prefix + b"\x01\x01\x00\xfe\xffx"
    trailer = struct.pack("<II", zlib.crc32(raw + b"x"), len(raw) + 1)
    packed = header + body + trailer
    return (
        gzip.compress(b"first member\n" * 30, mtime=0) + packed
        if kind == "later"
        else packed
    )


class _Source(io.BytesIO):
    def __init__(self, packed, fault):
        super().__init__(packed)
        self.fault = fault
        self.failure = OSError("original source read failure")
        self.calls = []
        self.started = False

    def read(self, size=-1):
        if len(self.calls) + 1 == self.fault:
            self.calls.append((size, "fault"))
            raise self.failure
        cap = 16384 if size >= 1024 and not self.started else 73
        if size >= 1024:
            self.started = True
        result = super().read(min(size, cap) if size >= 0 else cap)
        self.calls.append((size, len(result)))
        return result


def _history(codec, packed, method, budget, fault):
    source = _Source(packed, fault)
    pieces = []
    target = bytearray(b"\xa5" * budget)
    failure = None
    try:
        with codec.GzipFile(fileobj=source, mode="rb") as stream:
            pieces.append(stream.read1(16384))
            for _ in range(100000):
                if method == "readinto":
                    count = stream.readinto(target)
                    chunk = bytes(target[:count])
                else:
                    chunk = getattr(stream, method)(budget)
                if not chunk:
                    break
                pieces.append(chunk)
            else:
                pytest.fail("bounded gzip history did not terminate")
    except (OSError, EOFError, zlib.error) as error:
        failure = type(error), str(error), error is source.failure
    return pieces, failure, source.calls, source.tell(), source.closed, bytes(target)


@pytest.mark.parametrize(
    "kind", ["valid", "later", "truncated", "truncated-header", "bad-block"]
)
@pytest.mark.parametrize("method", ["read", "read1", "readinto"])
@pytest.mark.parametrize("budget", [7, 8192, 32768])
@pytest.mark.parametrize("fault", [0, 4])
def test_partial_dynamic_body_preserves_exact_public_history(
    kind, method, budget, fault
):
    packed = _member(kind)
    assert _history(gzipx, packed, method, budget, fault) == _history(
        gzip, packed, method, budget, fault
    )


def test_body_error_stays_visible_until_the_reader_is_reset():
    packed = _member("bad-block")
    for codec in (gzip, gzipx):
        with codec.GzipFile(fileobj=io.BytesIO(packed), mode="rb") as stream:
            for _ in range(3):
                with pytest.raises(zlib.error):
                    stream.read()
            stream.seek(0)
            with pytest.raises(zlib.error):
                stream.read()
