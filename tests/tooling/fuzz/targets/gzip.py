"""Gzip member/error fidelity with a streaming expansion guard.

Envelope: byte 0 modulo 4 selects raw gzip bytes, one generated member,
concatenated generated members, or generated CRC/truncation corruption. Remaining
bytes are compressed source (mode 0) or decoded content. The first payload byte
also selects compression level, stream chunk size and mode-3 corruption.

Before unbounded production decompression, stdlib GzipFile reads at most 65,537
decoded bytes. Inputs exceeding the 64-KiB decoded budget are harness refusals;
their declared ISIZE is also checked before decoding. ISIZE is only a preliminary
guard: the bounded stream is authoritative for forged sizes and concatenations.
Accepted streams check one-shot decode, incremental read/readinto, seek, caller
ownership, both compression interfaces, and exact stdlib exception families.
"""

from __future__ import annotations

import gzip as stdlib
import io
import zlib

from unstd.compress import gzipx


_LIMIT = 65536
_ERRORS = (stdlib.BadGzipFile, EOFError, zlib.error)


def _outcome(operation, *args):
    try:
        return ("value", operation(*args))
    except _ERRORS as error:
        return ("error", type(error))


def _bounded(raw):
    if (
        len(raw) >= 18
        and raw[:2] == b"\x1f\x8b"
        and int.from_bytes(raw[-4:], "little") > _LIMIT
    ):
        return False
    try:
        with stdlib.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as reader:
            return len(reader.read(_LIMIT + 1)) <= _LIMIT
    except _ERRORS:
        # A stream error before the output budget is exhausted is a bounded
        # negative case; an over-budget valid prefix returns False above.
        return True


def _stream(module, raw, chunk):
    source = io.BytesIO(raw)
    try:
        with module.GzipFile(fileobj=source, mode="rb") as reader:
            result = bytearray()
            buffer = bytearray(chunk)
            while count := reader.readinto(buffer):
                result.extend(buffer[:count])
                assert len(result) <= _LIMIT
            reader.seek(0)
            assert reader.read(7) == result[:7]
            reader.seek(0, 2)
            assert reader.tell() == len(result)
        return ("value", bytes(result))
    except _ERRORS as error:
        return ("error", type(error))
    finally:
        assert not source.closed


def fuzz(data: bytes) -> None:
    """Compare real codec/stream results after an independent bounded preflight."""
    mode = data[0] % 4 if data else 0
    payload = data[1:]
    selector = payload[0] if payload else 0
    level = selector % 10
    if mode == 0:
        raw = payload
    elif mode == 2:
        middle = len(payload) // 2
        raw = stdlib.compress(payload[:middle], mtime=0) + stdlib.compress(
            payload[middle:], mtime=0
        )
    else:
        raw = stdlib.compress(payload, mtime=0)
        if mode == 3:
            if selector % 2:
                raw = raw[:-1]
            else:
                raw = raw[:-8] + bytes([raw[-8] ^ 1]) + raw[-7:]
    if not _bounded(raw):
        return
    expected = _outcome(stdlib.decompress, raw)
    assert _outcome(gzipx.decompress, raw) == expected
    chunk = 257 + selector
    assert _stream(gzipx, raw, chunk) == _stream(stdlib, raw, chunk)
    if mode in (1, 2):
        assert expected == ("value", payload)
        encoded = gzipx.compress(payload, compresslevel=level, mtime=0)
        assert stdlib.decompress(encoded) == payload
        output = io.BytesIO()
        with gzipx.GzipFile(
            fileobj=output, mode="wb", compresslevel=level, mtime=0
        ) as writer:
            for offset in range(0, len(payload), chunk):
                assert writer.write(payload[offset : offset + chunk]) == len(
                    payload[offset : offset + chunk]
                )
            writer.flush()
        assert not output.closed
        assert stdlib.decompress(output.getvalue()) == payload
