"""Generated gzip laws with stdlib ``gzip``/``zlib`` as the oracle.

Law: any payload written through ``GzipFile`` in any partition, at any level,
is a stdlib-decodable member whose trailer is ``CRC32 || len mod 2**32``.
Law: stdlib members read back through any ``read(n)`` partition, each call
returning at most ``n`` bytes. Law: every proper non-empty prefix of a member
is rejected with stdlib's exception class and never decodes to data. Law:
concatenated members decode to the concatenated payloads.
"""

from __future__ import annotations

import gzip
import io
from itertools import pairwise
import struct
import zlib

from hypothesis import example, given
from hypothesis import strategies as st

from unstd.compress import gzipx


_PAYLOADS = st.binary(max_size=2048) | st.builds(
    lambda unit, n: unit * n, st.binary(min_size=1, max_size=8), st.integers(0, 600)
)
_LEVELS = st.integers(min_value=0, max_value=9)


def _partition(data: bytes, cuts: list[int]) -> list[bytes]:
    bounds = [0, *sorted({c % (len(data) + 1) for c in cuts}), len(data)]
    return [data[a:b] for a, b in pairwise(bounds)]


@given(
    payload=_PAYLOADS, level=_LEVELS, cuts=st.lists(st.integers(0, 4096), max_size=8)
)
@example(payload=b"", level=9, cuts=[])
@example(payload=b"\0" * 70000, level=1, cuts=[1, 65536])
def test_partitioned_writes_produce_one_stdlib_member(payload, level, cuts) -> None:
    out = io.BytesIO()
    with gzipx.GzipFile(fileobj=out, mode="wb", compresslevel=level, mtime=0) as handle:
        for part in _partition(payload, cuts):
            assert handle.write(part) == len(part)
    member = out.getvalue()
    assert gzip.decompress(member) == payload
    assert member[-8:] == struct.pack("<II", zlib.crc32(payload), len(payload))


@given(
    payload=_PAYLOADS, level=_LEVELS, sizes=st.lists(st.integers(1, 700), min_size=1)
)
@example(payload=b"\0" * 100000, level=9, sizes=[4096])
def test_partitioned_reads_are_bounded_and_exact(payload, level, sizes) -> None:
    member = gzip.compress(payload, level, mtime=0)
    assert gzipx.decompress(member) == payload
    got = bytearray()
    with gzipx.GzipFile(fileobj=io.BytesIO(member)) as handle:
        for index in range(len(payload) + 2):
            size = sizes[index % len(sizes)]
            chunk = handle.read(size)
            assert len(chunk) == min(size, len(payload) - len(got))
            if not chunk:
                break
            got += chunk
    assert got == payload


def _verdict(decode):
    try:
        return decode()
    except (OSError, EOFError, zlib.error) as error:
        return type(error)


@given(payload=st.binary(max_size=200), level=_LEVELS)
@example(payload=b"", level=6)
def test_every_proper_prefix_is_rejected_like_stdlib(payload, level) -> None:
    member = gzip.compress(payload, level, mtime=0)
    for end in range(1, len(member)):
        prefix = member[:end]
        expected = _verdict(lambda prefix=prefix: gzip.decompress(prefix))
        assert isinstance(expected, type), end
        assert _verdict(lambda prefix=prefix: gzipx.decompress(prefix)) is expected, end
        assert _verdict(
            lambda prefix=prefix: gzipx.GzipFile(fileobj=io.BytesIO(prefix)).read()
        ) is _verdict(
            lambda prefix=prefix: gzip.GzipFile(fileobj=io.BytesIO(prefix)).read()
        ), end


@given(
    members=st.lists(
        st.tuples(st.binary(max_size=300), _LEVELS), min_size=1, max_size=6
    )
)
def test_concatenated_members_decode_to_concatenated_payloads(members) -> None:
    stream = b"".join(gzip.compress(data, level, mtime=0) for data, level in members)
    expected = b"".join(data for data, _ in members)
    assert gzipx.decompress(stream) == expected
    with gzipx.GzipFile(fileobj=io.BytesIO(stream)) as handle:
        assert handle.read() == expected
