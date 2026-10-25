"""Native read execution, bounded expansion, and malformed stream histories."""

from __future__ import annotations

import gzip
import hashlib
import io
import runpy
import struct
import subprocess
import sys
import textwrap
import tracemalloc
from typing import TYPE_CHECKING
import zlib

import pytest


if TYPE_CHECKING:
    from types import FrameType


@pytest.fixture
def reader_module():
    pytest.importorskip("zlib_ng.zlib_ng")
    from unstd.compress import _reader

    return _reader


def test_reader_constructs_one_native_inflater_without_a_stdlib_reader(
    reader_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = io.BytesIO(gzip.compress(b"native read payload\n" * 100, mtime=0))
    factory = reader_module._factory
    created = []

    def native_factory(**options):
        result = factory(**options)
        created.append(result)
        return result

    def unexpected(*_args, **_kwargs):
        pytest.fail("native reader initialized a redundant stdlib inflater")

    monkeypatch.setattr(reader_module, "_factory", native_factory)
    monkeypatch.setattr(gzip._GzipReader, "__init__", unexpected)
    with reader_module._Reader(source) as reader:
        assert len(created) == 1
        assert reader.read(6) == b"native"
        assert len(created) == 1
    assert not source.closed


def test_reader_executes_the_real_native_decompress_method(reader_module) -> None:
    payload = b"actual native execution\n" * 100
    calls = []
    with reader_module._Reader(io.BytesIO(gzip.compress(payload))) as reader:
        inflater = vars(reader)["_decompressor"]
        if reader_module._bundled_inflater:
            assert type(inflater) is reader_module._accelerated.Inflater
        else:
            assert type(inflater).__module__.startswith("zlib_ng")

        def record(_frame: FrameType, event: str, method: object) -> None:
            if (
                event == "c_call"
                and getattr(method, "__name__", None) == "decompress"
                and getattr(method, "__self__", None) is inflater
            ):
                calls.append(method)

        previous = sys.getprofile()
        sys.setprofile(record)
        try:
            assert reader.read(31) == payload[:31]
        finally:
            sys.setprofile(previous)
    assert calls, "the selected native inflater did not execute decompression"


def test_reader_executes_native_crc_without_modifying_stdlib(reader_module) -> None:
    payload = b"native checksums\n" * 100
    packed = gzip.compress(payload)
    calls = []

    def record(_frame: FrameType, event: str, method: object) -> None:
        if event == "c_call" and method in (reader_module._crc32, zlib.crc32):
            calls.append(method)

    previous = sys.getprofile()
    sys.setprofile(record)
    try:
        with reader_module._Reader(io.BytesIO(packed)) as reader:
            assert reader.read() == payload
    finally:
        sys.setprofile(previous)
    assert reader_module._crc32 in calls
    assert zlib.crc32 not in calls
    assert gzip._GzipReader.read.__globals__["zlib"] is zlib
    if reader_module._bundled_inflater:
        assert reader_module._Reader.read.__code__ is gzip._GzipReader.read.__code__


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_platform_inflater_policy_retains_native_crc_and_error_family(
    reader_module, monkeypatch, platform
):
    monkeypatch.setattr(sys, "platform", platform)
    loaded = runpy.run_path(reader_module.__file__)
    bundled = loaded["_accelerated"]
    assert loaded["_bundled_inflater"] == (bundled is not None and platform != "linux")
    if bundled is not None:
        assert loaded["_crc32"] is bundled.crc32
    if not loaded["_bundled_inflater"]:
        native = loaded["_native"]
        expected = (
            native._ZlibDecompressor if loaded["_modern"] else native.decompressobj
        )
        assert loaded["_factory"] is expected
    invalid = b"\x1f\x8b\x08\x00" + bytes(6) + b"\x07" * 30
    with (
        loaded["_Reader"](io.BytesIO(invalid)) as reader,
        pytest.raises(zlib.error) as caught,
    ):
        reader.read()
    assert type(caught.value) is zlib.error


def test_missing_bundled_extension_keeps_the_portable_native_reader() -> None:
    pytest.importorskip("zlib_ng.zlib_ng")
    script = textwrap.dedent(
        """\
        import gzip
        import importlib.abc
        import io
        import sys

        class BlockBundled(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "unstd.compress._gzip_native":
                    raise ModuleNotFoundError("bundled extension unavailable")
                return None

        sys.meta_path.insert(0, BlockBundled())
        from unstd.compress import _reader, gzipx

        assert _reader._accelerated is None
        payload = b"portable native gzip reader\\n" * 100
        source = io.BytesIO(gzip.compress(payload[:70]) + gzip.compress(payload[70:]))
        with gzipx.GzipFile(fileobj=source) as handle:
            inflater = vars(handle._buffer.raw)["_decompressor"]
            assert type(inflater).__module__.startswith("zlib_ng")
            assert handle.read(17) == payload[:17]
            assert handle.seek(0) == 0
            assert handle.read() == payload
        assert not source.closed
        assert gzip._GzipReader.read.__globals__["zlib"].__name__ == "zlib"
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed bounded compatibility script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_small_read_keeps_expansion_bounded(reader_module) -> None:
    payload = b"large expanded record\n" * (512 * 1024)
    source = io.BytesIO(gzip.compress(payload, mtime=0))
    with io.BufferedReader(reader_module._Reader(source)) as reader:
        tracemalloc.start()
        try:
            assert reader.read(64) == payload[:64]
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 1024 * 1024
        assert reader.tell() == 64
        assert reader.seek(10) == 10
        assert reader.read(64) == payload[10:74]
    assert not source.closed


@pytest.mark.parametrize("size", [1, 31, 8192, 65536])
def test_native_bounded_reads_retain_every_decoded_byte(
    reader_module, size: int
) -> None:
    payload = b"".join(struct.pack("<I", index) for index in range(10000))
    with reader_module._Reader(io.BytesIO(gzip.compress(payload))) as reader:
        actual = b"".join(iter(lambda: reader.read(size), b""))
    assert actual == payload


@pytest.mark.parametrize("chunk", [8191, 128 * 1024])
def test_native_exact_input_boundary_continues_into_later_chunks(
    reader_module, chunk: int
) -> None:
    payload = hashlib.shake_256(b"native-gzip-input-boundary").digest(512 * 1024)
    packed = gzip.compress(payload, mtime=0)
    budget = min(chunk, getattr(gzip, "READ_BUFFER_SIZE", io.DEFAULT_BUFFER_SIZE))
    # Land exactly on the output produced by one compressed input chunk. Reaching
    # the output limit must request the next input, rather than make gzip mistake
    # an empty speculative drain for physical EOF.
    size = len(zlib.decompressobj(-15).decompress(packed[10 : 10 + budget]))

    class Chunked(io.BytesIO):
        def read(self, count=-1):
            return super().read(min(count, chunk) if count >= 0 else chunk)

    with reader_module._Reader(Chunked(packed)) as reader:
        actual = b"".join(iter(lambda: reader.read(size), b""))
    assert actual == payload


def _damaged_members() -> dict[str, bytes]:
    payload = b"a" * 9216
    member = gzip.compress(payload, mtime=0)
    # Stored, nonfinal DEFLATE followed by reserved BTYPE=3. The valid first
    # member exposes a C reader's tendency to merge member output before errors.
    block = b"\0" + struct.pack("<HH", len(payload), 65535 - len(payload))
    invalid_body = member[:10] + block + payload + b"\x07" + member[-8:]
    first = gzip.compress(b"first member\n" * 30, mtime=0)
    damaged = {
        "invalid-body": invalid_body,
        "crc": member[:-8] + bytes([member[-8] ^ 1]) + member[-7:],
        "length": member[:-4] + b"\0" * 4,
        "truncated-trailer": member[:-1],
        "junk-after-member": member + b"junk",
    }
    return {name: first + data for name, data in damaged.items()}


def _history(source, method: str, size: int):
    delivered = bytearray()
    try:
        with source:
            if method == "readinto":
                buffer = bytearray(size)
                while count := source.readinto(buffer):
                    delivered += buffer[:count]
            else:
                while chunk := getattr(source, method)(size):
                    delivered += chunk
    except (OSError, EOFError, zlib.error) as error:
        return bytes(delivered), type(error)
    return bytes(delivered), None


@pytest.mark.parametrize("case", list(_damaged_members()))
@pytest.mark.parametrize("method", ["read", "read1", "readinto"])
@pytest.mark.parametrize("size", [7, 8192, 65536, 131072])
def test_native_reader_preserves_bytes_delivered_before_member_failure(
    reader_module, case: str, method: str, size: int
) -> None:
    packed = _damaged_members()[case]
    expected = _history(gzip.GzipFile(fileobj=io.BytesIO(packed)), method, size)
    actual = _history(
        io.BufferedReader(reader_module._Reader(io.BytesIO(packed))), method, size
    )
    assert actual == expected
    assert expected[1] is not None
