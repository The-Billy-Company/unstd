"""RFC 1952 member framing and multi-member stream verdicts against stdlib ``gzip``.

Compressed DEFLATE bytes may differ between engines, but the 10-byte header,
optional FNAME field and 8-byte CRC32/ISIZE trailer have fixed meanings. Fixed
fields are hand-packed here; XFL, whose value is an engine convention, must
match stdlib. Multi-member damage is compared with stdlib on bytes delivered
before the failure and on the exception class.
"""

from __future__ import annotations

import gzip
import io
import struct
import zlib

import pytest

from unstd.compress import gzipx


PAYLOAD = b"multi-member payload\n" * 40 + bytes(range(256))


def _header(*, flags: int = 0, mtime: int, xfl: int) -> bytes:
    return b"\x1f\x8b\x08" + struct.pack("<BIBB", flags, mtime, xfl, 255)


def _trailer(data: bytes) -> bytes:
    return struct.pack("<II", zlib.crc32(data), len(data) % 2**32)


@pytest.mark.parametrize("level", range(10))
def test_one_shot_member_frames_match_rfc1952(level) -> None:
    member = gzipx.compress(PAYLOAD, level, mtime=0x01020304)
    xfl = gzip.compress(PAYLOAD, level, mtime=0)[8]
    assert xfl in {0, 2, 4}
    assert member[:10] == _header(mtime=0x01020304, xfl=xfl)
    assert member[-8:] == _trailer(PAYLOAD)
    assert zlib.decompress(member[10:-8], wbits=-15) == PAYLOAD
    assert gzipx.compress(PAYLOAD, level, mtime=0x01020304) == member


@pytest.mark.parametrize("level", [1, 6, 9])
@pytest.mark.parametrize(
    ("name", "field"),
    [
        ("dir/report.txt.gz", b"report.txt"),
        ("plain", b"plain"),
        ("", b""),
        ("caf\u00e9.gz", b"caf\xe9"),
        ("emoji-\U0001f600.gz", b""),
    ],
    ids=["strips-dir-and-gz", "bare", "empty", "latin-1", "not-latin-1"],
)
def test_file_member_header_carries_fname_and_trailer(level, name, field) -> None:
    out = io.BytesIO()
    with gzipx.GzipFile(
        name, mode="wb", fileobj=out, compresslevel=level, mtime=7
    ) as handle:
        for start in range(0, len(PAYLOAD), 97):
            handle.write(PAYLOAD[start : start + 97])
    reference = io.BytesIO()
    with gzip.GzipFile(
        name, mode="wb", fileobj=reference, compresslevel=level, mtime=7
    ) as handle:
        handle.write(PAYLOAD)
    xfl = reference.getvalue()[8]
    prefix = _header(flags=8 if field else 0, mtime=7, xfl=xfl)
    prefix += field + b"\0" if field else b""
    data = out.getvalue()
    assert data.startswith(prefix)
    assert reference.getvalue().startswith(prefix)
    assert data[-8:] == _trailer(PAYLOAD)
    assert zlib.decompress(data[len(prefix) : -8], wbits=-15) == PAYLOAD


def test_file_writes_are_deterministic_for_fixed_mtime() -> None:
    def member() -> bytes:
        out = io.BytesIO()
        with gzipx.GzipFile(fileobj=out, mode="wb", mtime=0) as handle:
            handle.write(PAYLOAD[:500])
            handle.write(PAYLOAD[500:])
        return out.getvalue()

    assert member() == member()


def test_empty_and_mixed_level_members_concatenate() -> None:
    parts = [b"", b"first\n", b"", PAYLOAD, b"last"]
    stream = b"".join(
        gzip.compress(part, level, mtime=index)
        for index, (part, level) in enumerate(zip(parts, [0, 1, 9, 6, 3], strict=True))
    )
    assert gzipx.decompress(stream) == b"".join(parts)
    with gzipx.GzipFile(fileobj=io.BytesIO(stream)) as handle:
        assert b"".join(iter(lambda: handle.read(5), b"")) == b"".join(parts)


def _damaged_second_member() -> dict[str, bytes]:
    first = gzip.compress(b"first member\n" * 30, mtime=0)
    second = gzip.compress(PAYLOAD, mtime=0)
    crc = second[:-8] + bytes([second[-8] ^ 0x80]) + second[-7:]
    isize = second[:-4] + struct.pack("<I", len(PAYLOAD) + 1)
    return {
        "crc": first + crc,
        "isize": first + isize,
        "truncated-body": first + second[: len(second) // 2],
        "truncated-trailer": first + second[:-3],
        "bad-method": first + second[:2] + b"\x07" + second[3:],
        "bad-magic": first + b"\x1f\x8c" + second[2:],
    }


def _drain(module, data: bytes):
    """Bytes delivered in 7-byte reads until EOF or failure, and the failure class."""
    delivered = bytearray()
    try:
        with module.GzipFile(fileobj=io.BytesIO(data)) as handle:
            while chunk := handle.read(7):
                delivered += chunk
    except (OSError, EOFError, zlib.error) as error:
        return bytes(delivered), type(error)
    return bytes(delivered), None


def test_invalid_deflate_keeps_stdlib_buffered_delivery_history() -> None:
    payload = b"a" * 9216
    # A nonfinal stored block followed by reserved BTYPE=3. A smaller read-ahead
    # budget can expose valid bytes before the corruption that stdlib sees first.
    block = b"\0" + struct.pack("<HH", len(payload), 65535 - len(payload))
    packed = _header(mtime=0, xfl=0) + block + payload + b"\x07" + _trailer(payload)
    expected = _drain(gzip, packed)
    assert expected[1] is zlib.error
    assert _drain(gzipx, packed) == expected


@pytest.mark.parametrize("case", list(_damaged_second_member()))
def test_damage_in_a_later_member_matches_stdlib(case) -> None:
    data = _damaged_second_member()[case]
    with pytest.raises((OSError, EOFError, zlib.error)) as oracle:
        gzip.decompress(data)
    with pytest.raises(type(oracle.value)):
        gzipx.decompress(data)
    delivered, failure = _drain(gzipx, data)
    assert (delivered, failure) == _drain(gzip, data)
    assert failure is type(oracle.value)
    assert (b"first member\n" * 30 + PAYLOAD).startswith(delivered)
