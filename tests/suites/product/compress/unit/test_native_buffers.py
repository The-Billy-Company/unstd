"""Native gzip buffers expose bytes only after whole-member validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import inspect
import struct
import sys
from threading import Barrier
import tracemalloc
from typing import TYPE_CHECKING
import zlib

import pytest

from tests.support.environment.backends import require_backend


if TYPE_CHECKING:
    from types import FrameType, ModuleType


@pytest.fixture
def native_buffers() -> ModuleType:
    return require_backend("unstd.compress._gzip_native")


@pytest.mark.parametrize("size", [0, 1, 15, 16, 255, 256, 65535, 65536, 262145])
@pytest.mark.parametrize("initial", [0, 1, -1, 0xFFFFFFFF, 2**65 + 7])
def test_native_ieee_crc_matches_stdlib_and_incremental_updates(
    native_buffers: ModuleType, size: int, initial: int
) -> None:
    payload = (bytes(range(256)) * ((size + 255) // 256))[:size]
    assert native_buffers.crc32(payload, initial) == zlib.crc32(payload, initial)
    boundary = size // 2
    prefix = native_buffers.crc32(payload[:boundary], initial)
    assert native_buffers.crc32(payload[boundary:], prefix) == zlib.crc32(
        payload, initial
    )
    assert native_buffers.crc32(b"123456789") == 0xCBF43926


@pytest.mark.parametrize("level", [0, 1, 9])
@pytest.mark.parametrize("size", [0, 1, 256, 1024 * 1024])
def test_native_accepts_complete_members_and_returns_exact_bytes(
    native_buffers: ModuleType, level: int, size: int
) -> None:
    payload = (bytes(range(256)) * ((size + 255) // 256))[:size]
    packed = gzip.compress(payload, compresslevel=level, mtime=123)
    actual = native_buffers.decompress(packed)
    assert type(actual) is bytes
    assert actual == gzip.decompress(packed) == payload


def test_repeated_mixed_members_remain_independent_across_threads(
    native_buffers: ModuleType,
) -> None:
    short = b"a short fixed Huffman member\n" * 20
    records = b"".join(struct.pack("<I", index) for index in range(4000))
    payloads = (short, records, records[::-1], records * 16)
    members = tuple(
        gzip.compress(payload, compresslevel=level, mtime=0)
        for payload, level in zip(payloads, (1, 0, 9, 9), strict=True)
    )
    corrupt = members[2][:-8] + bytes([members[2][-8] ^ 1]) + members[2][-7:]
    invalid = members[0][:10] + b"\x07" + members[0][-8:]
    ready = Barrier(4)

    def roundtrip(worker: int) -> None:
        ready.wait(timeout=30)
        for turn in range(8):
            first = (worker + turn) % len(members)
            for position in (first, 3, 2, 1, 0):
                actual = native_buffers.decompress(members[position])
                assert actual == payloads[position]
            assert native_buffers.decompress(corrupt) is None
            assert native_buffers.decompress(invalid) is None
            assert native_buffers.decompress(members[0]) == short

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(roundtrip, range(4)))


def test_valid_large_buffer_executes_the_compiled_decoder(
    native_buffers: ModuleType,
) -> None:
    payload = bytes(range(256)) * 4096
    packed = gzip.compress(payload, mtime=0)
    decoder = native_buffers.decompress
    assert inspect.isbuiltin(decoder)
    assert decoder.__module__ == "unstd.compress._gzip_native"
    calls = []

    def record(_frame: FrameType, event: str, method: object) -> None:
        if event == "c_call" and method is decoder:
            calls.append(method)

    previous = sys.getprofile()
    sys.setprofile(record)
    try:
        actual = decoder(packed)
    finally:
        sys.setprofile(previous)
    assert actual == payload
    assert calls, "the compiled gzip buffer decoder did not execute"


def _damaged_members() -> dict[str, bytes]:
    payload = b"the entire payload must pass validation\n" * 100
    packed = gzip.compress(payload, mtime=0)
    return {
        "crc": packed[:-8] + bytes([packed[-8] ^ 1]) + packed[-7:],
        "isize": packed[:-4] + struct.pack("<I", len(payload) - 1),
        "truncated-trailer": packed[:-1],
        "invalid-deflate": packed[:10] + b"\x07" + packed[-8:],
        "trailing-junk": packed + b"junk",
    }


@pytest.mark.parametrize("case", list(_damaged_members()))
def test_damaged_members_never_expose_unvalidated_output(
    native_buffers: ModuleType, case: str
) -> None:
    assert native_buffers.decompress(_damaged_members()[case]) is None


@pytest.mark.parametrize("second", ["valid", "crc", "isize", "truncated-trailer"])
def test_concatenated_members_never_return_an_accepted_prefix(
    native_buffers: ModuleType, second: str
) -> None:
    first = gzip.compress(b"first member\n" * 100, mtime=0)
    last = (
        gzip.compress(b"second member\n" * 100, mtime=0)
        if second == "valid"
        else _damaged_members()[second]
    )
    assert native_buffers.decompress(first + last) is None


def test_concatenated_members_with_equal_size_do_not_pass_the_single_member_proof(
    native_buffers: ModuleType,
) -> None:
    first = gzip.compress(b"first" * 100, mtime=0)
    second = gzip.compress(b"other" * 100, mtime=0)
    assert first[-4:] == second[-4:]
    assert native_buffers.decompress(first + second) is None


def test_native_declines_other_buffers_without_invoking_application_hooks(
    native_buffers: ModuleType,
) -> None:
    class BytesSubclass(bytes):
        def __bytes__(self):
            pytest.fail("native rejection invoked a bytes subclass conversion")

        def __getitem__(self, _key):
            pytest.fail("native rejection sliced a bytes subclass")

    class BufferLike:
        def __bytes__(self):
            pytest.fail("native rejection invoked an application conversion")

        def __len__(self):
            pytest.fail("native rejection invoked an application length callback")

    packed = gzip.compress(b"nonexact bytes use the public compatibility path")
    for unsupported in (
        bytearray(packed),
        memoryview(packed),
        memoryview(packed)[::2],
        BytesSubclass(packed),
        BufferLike(),
        None,
        "not bytes",
    ):
        assert native_buffers.decompress(unsupported) is None


@pytest.mark.parametrize("claimed_size", [8 * 1024 * 1024, 2**32 - 1])
def test_forged_trailer_does_not_allocate_the_claimed_output(
    native_buffers: ModuleType, claimed_size: int
) -> None:
    packed = gzip.compress(b"x", mtime=0)
    assert native_buffers.decompress(packed) == b"x"
    forged = packed[:-4] + struct.pack("<I", claimed_size)
    tracemalloc.start()
    try:
        assert native_buffers.decompress(forged) is None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 256 * 1024, "a tiny invalid member reserved its forged output size"


@pytest.mark.parametrize("case", ["reserved-flags", "crc", "isize", "invalid-deflate"])
def test_public_buffer_rejection_preserves_the_stdlib_result_and_diagnostics(
    native_buffers: ModuleType, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_backend("zlib_ng.zlib_ng")
    from unstd.compress import gzipx

    if case == "reserved-flags":
        packed = bytearray(gzip.compress(b"stdlib accepts reserved flags", mtime=0))
        packed[3] |= 0x20
        data = bytes(packed)
    else:
        data = _damaged_members()[case]
    assert native_buffers.decompress(data) is None
    attempted = []

    def decode(value):
        attempted.append(value)
        return native_buffers.decompress(value)

    def outcome(operation):
        try:
            return operation(data)
        except (OSError, EOFError, zlib.error) as error:
            return type(error), error.args

    monkeypatch.setattr(gzipx, "_decode_buffer", decode)
    assert outcome(gzipx.decompress) == outcome(gzip.decompress)
    assert attempted == [data]
