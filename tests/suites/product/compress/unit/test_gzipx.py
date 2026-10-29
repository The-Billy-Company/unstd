"""Gzip interoperability, stdlib behavior, and real compressed NDJSON streams.

Compressed bytes may differ between engines. These tests compare decoded values,
headers with defined meaning, error families, and file behavior against stdlib
``gzip`` on the interpreter running the test.
"""

from __future__ import annotations

from array import array
import gzip
import inspect
import io
import json
import struct
import subprocess
import sys
import textwrap
import time
from typing import TYPE_CHECKING
import zlib

import pytest


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


PAYLOAD = b"first line\nsecond line\n" + bytes(range(256)) * 16
MEMBER = gzip.compress(PAYLOAD, mtime=123)


@pytest.fixture
def gzip_module() -> ModuleType:
    """The public module, exercising its native backend or real stdlib fallback."""
    from unstd.compress import gzipx

    return gzipx


def _mtime(data: bytes) -> int:
    """The RFC 1952 little-endian timestamp field."""
    return struct.unpack_from("<I", data, 4)[0]


def _read(module, data: bytes, method: str) -> bytes:
    """Drain a caller-owned compressed stream through one public read operation."""
    with module.GzipFile(fileobj=io.BytesIO(data), mode="rb") as handle:
        if method == "read":
            return handle.read()
        if method in {"read1", "readline"}:
            read = getattr(handle, method)
            return b"".join(iter(read, b""))
        if method in {"readinto", "readinto1"}:
            readinto = getattr(handle, method)
            buffer = bytearray(37)
            chunks = []
            while count := readinto(buffer):
                chunks.append(bytes(buffer[:count]))
            return b"".join(chunks)
        if method == "iteration":
            return b"".join(handle)
        if method == "peek":
            handle.peek(1)
            return handle.read()
        if method == "seek":
            handle.seek(0, io.SEEK_END)
            handle.seek(0)
            return handle.read()
        raise AssertionError(method)


def _outcome(call):
    """The result or concrete exception family; error wording is backend-specific."""
    try:
        return call()
    except (OSError, EOFError, zlib.error, TypeError, ValueError) as error:
        return type(error)


READ_METHODS = [
    "read",
    "read1",
    "readinto",
    "readinto1",
    "readline",
    "iteration",
    "peek",
    "seek",
]
CORRUPT_STREAMS = [
    pytest.param(b"", id="empty"),
    pytest.param(b"\x1f", id="one-byte-magic"),
    pytest.param(b"not gzip", id="short-invalid-header"),
    pytest.param(b"this is not a gzip stream", id="invalid-header"),
    pytest.param(MEMBER[:2] + b"\0" + MEMBER[3:], id="invalid-method"),
    pytest.param(MEMBER[:10] + b"\x07" + MEMBER[-8:], id="reserved-deflate-block"),
    pytest.param(MEMBER[:-8] + bytes([MEMBER[-8] ^ 1]) + MEMBER[-7:], id="bad-crc"),
    pytest.param(MEMBER[:-4] + b"\0\0\0\0", id="bad-size"),
    pytest.param(MEMBER[:8], id="truncated-header"),
    pytest.param(MEMBER[:-1], id="truncated-trailer"),
    pytest.param(MEMBER + b"junk", id="junk-after-member"),
    pytest.param(MEMBER + b"\x1f", id="partial-next-header"),
]


def test_public_names_and_exception_identity(gzip_module) -> None:
    assert set(gzip_module.__all__) == {
        "open",
        "GzipFile",
        "compress",
        "decompress",
        "BadGzipFile",
    }
    assert gzip_module.BadGzipFile is gzip.BadGzipFile


def test_installed_backend_selects_the_accelerated_surface(gzip_module) -> None:
    pytest.importorskip("zlib_ng.gzip_ng")
    assert gzip_module.compress is not gzip.compress
    assert gzip_module.decompress is not gzip.decompress
    assert gzip_module.GzipFile is not gzip.GzipFile


@pytest.mark.parametrize("level", range(10))
def test_every_stdlib_level_produces_interoperable_members(gzip_module, level) -> None:
    compressed = gzip_module.compress(PAYLOAD, compresslevel=level, mtime=123)
    assert type(compressed) is bytes
    assert _mtime(compressed) == 123
    assert gzip.decompress(compressed) == PAYLOAD
    assert (
        gzip_module.decompress(gzip.compress(PAYLOAD, compresslevel=level)) == PAYLOAD
    )
    if level == 0:
        assert len(compressed) >= len(PAYLOAD)


@pytest.mark.parametrize("make_buffer", [bytes, bytearray, memoryview])
def test_one_shot_calls_accept_bytes_like_objects(gzip_module, make_buffer) -> None:
    compressed = gzip_module.compress(make_buffer(PAYLOAD), mtime=0)
    assert gzip.decompress(compressed) == PAYLOAD
    assert gzip_module.decompress(make_buffer(MEMBER)) == PAYLOAD


def test_default_compression_level_matches_stdlib_on_every_surface(gzip_module) -> None:
    level = inspect.signature(gzip.open).parameters["compresslevel"].default
    assert (
        inspect.signature(gzip_module.open).parameters["compresslevel"].default == level
    )
    assert (
        inspect.signature(gzip_module.GzipFile).parameters["compresslevel"].default
        == level
    )
    assert (
        inspect.signature(gzip_module.compress).parameters["compresslevel"].default
        == level
    )
    assert (
        gzip_module.compress(PAYLOAD, mtime=0)[8] == gzip.compress(PAYLOAD, mtime=0)[8]
    )
    output = io.BytesIO()
    with gzip_module.open(output, "wb") as handle:
        handle.write(PAYLOAD)
    expected = io.BytesIO()
    with gzip.open(expected, "wb") as handle:
        handle.write(PAYLOAD)
    assert output.getvalue()[8] == expected.getvalue()[8]


def test_omitted_mtime_matches_this_interpreters_stdlib_default(gzip_module) -> None:
    default = inspect.signature(gzip.compress).parameters["mtime"].default
    assert (
        inspect.signature(gzip_module.compress).parameters["mtime"].default == default
    )
    before = int(time.time())
    stamp = _mtime(gzip_module.compress(PAYLOAD))
    after = int(time.time())
    assert stamp == 0 if default == 0 else before <= stamp <= after


@pytest.mark.parametrize("mtime", [None, 0, 123, 2**32 - 1])
def test_explicit_mtime_has_stdlib_meaning(gzip_module, mtime) -> None:
    before = int(time.time())
    compressed = gzip_module.compress(PAYLOAD, mtime=mtime)
    after = int(time.time())
    stamp = _mtime(compressed)
    assert before <= stamp <= after if mtime is None else stamp == mtime
    if sys.version_info >= (3, 13):
        assert compressed[9] == 255


@pytest.mark.parametrize("level", range(10))
def test_gzipfile_supports_levels_fileobj_and_binary_buffers(
    gzip_module, level
) -> None:
    output = io.BytesIO()
    with gzip_module.GzipFile(
        filename="payload.gz", mode="wb", fileobj=output, compresslevel=level, mtime=123
    ) as handle:
        assert isinstance(handle, gzip.GzipFile)
        assert handle.write(bytearray(PAYLOAD[:123])) == 123
        assert handle.write(memoryview(PAYLOAD[123:])) == len(PAYLOAD) - 123
        assert handle.tell() == len(PAYLOAD)
    assert not output.closed
    assert gzip.decompress(output.getvalue()) == PAYLOAD
    assert _mtime(output.getvalue()) == 123


@pytest.mark.parametrize("method", READ_METHODS)
def test_each_read_operation_decodes_stdlib_members(gzip_module, method) -> None:
    assert _read(gzip_module, MEMBER, method) == _read(gzip, MEMBER, method) == PAYLOAD


def test_peek_seek_tell_and_read_sizes_match_stdlib(gzip_module) -> None:
    with gzip_module.GzipFile(fileobj=io.BytesIO(MEMBER), mode="rb") as handle:
        assert handle.readable()
        assert not handle.writable()
        assert handle.seekable()
        assert handle.tell() == 0
        assert handle.peek(5).startswith(PAYLOAD[:5])
        assert handle.tell() == 0
        assert handle.read(5) == PAYLOAD[:5]
        assert handle.tell() == 5
        assert handle.seek(20) == 20
        assert handle.read(7) == PAYLOAD[20:27]
        assert handle.seek(-4, io.SEEK_CUR) == 23
        assert handle.read(2) == PAYLOAD[23:25]
        assert handle.seek(-8, io.SEEK_END) == len(PAYLOAD) - 8
        assert handle.read() == PAYLOAD[-8:]
        handle.rewind()
        assert handle.read(0) == b""
        assert handle.read(None) == PAYLOAD
        assert handle.mtime == 123


def test_forward_write_seek_fills_with_zeroes(gzip_module) -> None:
    output = io.BytesIO()
    with gzip_module.GzipFile(fileobj=output, mode="wb", mtime=0) as handle:
        assert handle.write(b"first") == 5
        handle.seek(10)
        assert handle.tell() == 10
        assert handle.write(b"last") == 4
        assert handle.tell() == 14
        with pytest.raises(OSError, match="Negative seek"):
            handle.seek(1)
    assert gzip.decompress(output.getvalue()) == b"first" + b"\0" * 5 + b"last"


@pytest.mark.parametrize("text_mode", [False, True])
def test_open_accepts_paths_and_keeps_streams_open_during_use(
    gzip_module, tmp_path: Path, text_mode
) -> None:
    path = tmp_path / "payload.gz"
    value = "café\nsecond line\n" if text_mode else PAYLOAD
    options = {"encoding": "utf-8"} if text_mode else {}
    with gzip_module.open(path, "wt" if text_mode else "wb", **options) as handle:
        assert handle.write(value) == len(value)
        assert not handle.closed
    assert handle.closed
    with gzip.open(path, "rt" if text_mode else "rb", **options) as oracle:
        assert oracle.read() == value
    with gzip_module.open(path, "rt" if text_mode else "rb", **options) as handle:
        assert handle.read() == value
    assert handle.closed


@pytest.mark.parametrize("text_mode", [False, True])
def test_open_does_not_close_a_caller_owned_fileobj(gzip_module, text_mode) -> None:
    output = io.BytesIO()
    value = "café\n" if text_mode else PAYLOAD
    options = {"encoding": "utf-8"} if text_mode else {}
    with gzip_module.open(output, "wt" if text_mode else "wb", **options) as handle:
        handle.write(value)
    assert not output.closed
    output.seek(0)
    with gzip_module.open(output, "rt" if text_mode else "rb", **options) as handle:
        assert handle.read() == value
    assert not output.closed


def test_open_text_encoding_newlines_and_error_policy(
    gzip_module, tmp_path: Path
) -> None:
    path = tmp_path / "text.gz"
    with gzip_module.open(path, "wt", encoding="utf-16", newline="\r\n") as handle:
        handle.write("café\nnext\n")
    with gzip.open(path, "rt", encoding="utf-16", newline="") as oracle:
        assert oracle.read() == "café\r\nnext\r\n"
    with gzip_module.open(path, "rt", encoding="utf-16", newline=None) as handle:
        assert list(handle) == ["café\n", "next\n"]
    path.write_bytes(gzip.compress(b"before\xffafter"))
    with gzip_module.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        assert handle.read() == "before\ufffdafter"


def test_append_and_exclusive_create_keep_existing_data(
    gzip_module, tmp_path: Path
) -> None:
    path = tmp_path / "append.gz"
    with gzip_module.open(path, "xb") as handle:
        handle.write(b"first\n")
    with pytest.raises(FileExistsError):
        gzip_module.open(path, "xb")
    with gzip_module.open(path, "ab") as handle:
        handle.write(b"second\n")
    assert gzip.decompress(path.read_bytes()) == b"first\nsecond\n"
    with gzip_module.open(path, "rb") as handle:
        assert list(handle) == [b"first\n", b"second\n"]


def test_flush_allows_more_writes_and_emits_a_valid_final_stream(gzip_module) -> None:
    output = io.BytesIO()
    with gzip_module.GzipFile(fileobj=output, mode="wb", mtime=0) as handle:
        handle.write(b"first")
        handle.flush()
        assert output.getvalue().startswith(b"\x1f\x8b")
        partial = zlib.decompressobj(wbits=31)
        assert partial.decompress(output.getvalue()) == b"first"
        assert not partial.eof
        handle.write(b"second")
        handle.flush(zlib.Z_SYNC_FLUSH)
        assert handle.tell() == 11
    assert gzip.decompress(output.getvalue()) == b"firstsecond"


def test_closed_and_wrong_direction_operations_keep_stdlib_error_families(
    gzip_module,
) -> None:
    reader = gzip_module.GzipFile(fileobj=io.BytesIO(MEMBER), mode="rb")
    with pytest.raises(OSError, match="read-only"):
        reader.write(b"forbidden")
    reader.close()
    for operation in [
        lambda: reader.read(),
        lambda: reader.peek(1),
        lambda: reader.seek(0),
    ]:
        with pytest.raises(ValueError, match="closed"):
            operation()
    writer = gzip_module.GzipFile(fileobj=io.BytesIO(), mode="wb")
    with pytest.raises(OSError, match="write-only"):
        writer.read()
    writer.close()
    with pytest.raises(ValueError, match="closed"):
        writer.write(b"closed")


def test_concatenated_members_and_zero_padding_decode_completely(gzip_module) -> None:
    data = (
        gzip.compress(b"first\n") + b"\0" * 7 + gzip.compress(b"second\n") + b"\0" * 3
    )
    assert gzip_module.decompress(data) == gzip.decompress(data) == b"first\nsecond\n"
    assert _read(gzip_module, data, "iteration") == b"first\nsecond\n"


def test_optional_header_fields_remain_interoperable(gzip_module) -> None:
    header = bytearray(MEMBER[:10])
    header[3] = 4 | 8 | 16
    data = (
        bytes(header)
        + struct.pack("<H", 3)
        + b"abc"
        + b"payload.txt\0"
        + b"test comment\0"
        + MEMBER[10:]
    )
    assert gzip_module.decompress(data) == gzip.decompress(data) == PAYLOAD
    assert _read(gzip_module, data, "read") == PAYLOAD


@pytest.mark.parametrize("valid_checksum", [False, True])
@pytest.mark.parametrize("flags", [0, 4, 8, 16, 4 | 8 | 16])
def test_header_checksum_policy_matches_stdlib(
    gzip_module, valid_checksum, flags
) -> None:
    header = bytearray(MEMBER[:10])
    header[3] = 2 | flags
    if flags & 4:
        header += struct.pack("<H", 3) + b"abc"
    if flags & 8:
        header += b"payload.txt\0"
    if flags & 16:
        header += b"test comment\0"
    checksum = zlib.crc32(header) & 0xFFFF
    if not valid_checksum:
        checksum ^= 1
    data = bytes(header) + struct.pack("<H", checksum) + MEMBER[10:]
    if _outcome(lambda: gzip.decompress(data)) is gzip.BadGzipFile:
        with pytest.raises(gzip.BadGzipFile) as expected_error:
            gzip.decompress(data)
        for decode in (
            gzip_module.decompress,
            lambda value: _read(gzip_module, value, "read"),
        ):
            with pytest.raises(gzip.BadGzipFile) as actual:
                decode(data)
            assert str(actual.value) == str(expected_error.value)
    else:
        expected = gzip.decompress(data)
        assert expected == PAYLOAD
        assert gzip_module.decompress(data) == expected
        assert _read(gzip_module, data, "read") == _read(gzip, data, "read") == expected


def test_native_one_shot_defers_header_checksum_policy_to_the_interpreter():
    native = pytest.importorskip("unstd.compress._gzip_native")
    header = bytearray(MEMBER[:10])
    header[3] = 2
    checksum = zlib.crc32(header) & 0xFFFF
    for value in (checksum, checksum ^ 1):
        data = bytes(header) + struct.pack("<H", value) + MEMBER[10:]
        assert native.decompress(data) is None


@pytest.mark.parametrize("data", CORRUPT_STREAMS)
def test_one_shot_malformed_inputs_match_stdlib_error_families(
    gzip_module, data
) -> None:
    assert _outcome(lambda: gzip_module.decompress(data)) == _outcome(
        lambda: gzip.decompress(data)
    )


@pytest.mark.parametrize("data", CORRUPT_STREAMS)
def test_one_shot_errors_retain_exact_stdlib_diagnostics(gzip_module, data) -> None:
    try:
        expected = gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as error:
        error_type, error_message = type(error), str(error)
    else:
        assert gzip_module.decompress(data) == expected
        return
    with pytest.raises(error_type) as caught:
        gzip_module.decompress(data)
    assert str(caught.value) == error_message


@pytest.mark.parametrize("flag", [0x20, 0x40, 0x80])
def test_reserved_header_flags_keep_stdlib_acceptance(gzip_module, flag) -> None:
    header = bytearray(MEMBER)
    header[3] |= flag
    assert gzip_module.decompress(header) == gzip.decompress(header) == PAYLOAD


@pytest.mark.parametrize("data", [None, 0, False, [], "gzip", object()])
def test_non_buffer_inputs_keep_stdlib_outcomes(gzip_module, data) -> None:
    # In particular, BytesIO(None) is empty: gzip historically accepts None.
    assert _outcome(lambda: gzip_module.decompress(data)) == _outcome(
        lambda: gzip.decompress(data)
    )


def test_one_shot_noncontiguous_buffer_keeps_stdlib_error(gzip_module) -> None:
    data = memoryview(MEMBER)[::2]
    with pytest.raises(BufferError) as expected:
        gzip.decompress(data)
    with pytest.raises(type(expected.value)) as caught:
        gzip_module.decompress(data)
    assert str(caught.value) == str(expected.value)


@pytest.mark.parametrize("shape", ["integers", "array", "multidimensional"])
def test_element_indexed_buffers_keep_stdlib_slice_semantics(
    gzip_module, shape
) -> None:
    raw = gzip.compress(bytes(range(128)), mtime=0)
    raw += b"\0" * (-len(raw) % 4)
    data = (
        memoryview(raw).cast("I")
        if shape == "integers"
        else array("I", raw)
        if shape == "array"
        else memoryview(raw).cast("B", shape=(len(raw) // 4, 4))
    )
    assert _outcome(lambda: gzip_module.decompress(data)) == _outcome(
        lambda: gzip.decompress(data)
    )


def test_input_subclass_slice_callbacks_propagate_unchanged(gzip_module) -> None:
    failure = RuntimeError("application buffer slice failed")

    class SlicedBytes(bytes):
        def __getitem__(self, key):
            if isinstance(key, slice):
                raise failure
            return super().__getitem__(key)

    for module in (gzip, gzip_module):
        with pytest.raises(RuntimeError) as caught:
            module.decompress(SlicedBytes(MEMBER))
        assert caught.value is failure


@pytest.mark.parametrize("method", READ_METHODS)
@pytest.mark.parametrize("data", CORRUPT_STREAMS)
def test_stream_malformed_inputs_match_stdlib_error_families(
    gzip_module, data, method
) -> None:
    assert _outcome(lambda: _read(gzip_module, data, method)) == _outcome(
        lambda: _read(gzip, data, method)
    )


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "rbt"},
        {"mode": "invalid"},
        {"mode": "rb", "encoding": "utf-8"},
        {"mode": "rb", "errors": "ignore"},
        {"mode": "rb", "newline": "\n"},
    ],
)
def test_invalid_open_options_match_stdlib(gzip_module, options) -> None:
    def attempt(module):
        with module.open(io.BytesIO(MEMBER), **options) as handle:
            return handle.read()

    assert _outcome(lambda: attempt(gzip_module)) == _outcome(lambda: attempt(gzip))


@pytest.mark.parametrize("level", [-2, 10, "fast", None])
def test_invalid_compression_levels_preserve_existing_exception_catches(
    gzip_module, level
) -> None:
    def outcome(module):
        try:
            module.compress(PAYLOAD, compresslevel=level, mtime=0)
        except (TypeError, ValueError, zlib.error) as error:
            return type(error)
        message = "invalid compression level was accepted"
        raise AssertionError(message)

    assert outcome(gzip_module) is outcome(gzip)


@pytest.mark.parametrize("level", [-2, 10, "fast", None])
def test_invalid_stream_compression_levels_match_stdlib(gzip_module, level) -> None:
    def attempt(module):
        with module.GzipFile(
            fileobj=io.BytesIO(), mode="wb", compresslevel=level
        ) as handle:
            return handle.write(PAYLOAD)

    assert _outcome(lambda: attempt(gzip_module)) == _outcome(lambda: attempt(gzip))


@pytest.mark.parametrize("text_mode", [False, True])
def test_untyped_ndjson_pipeline_uses_real_gzip_files(
    gzip_module, tmp_path: Path, text_mode
) -> None:
    from unstd.serde import ndjson

    path = tmp_path / "rows.jsonl.gz"
    rows = [{"idx": 1, "name": "café"}, {"idx": 2, "values": [True, None]}]
    options = {"encoding": "utf-8"} if text_mode else {}
    with gzip_module.open(path, "wt" if text_mode else "wb", **options) as handle:
        assert ndjson.dump(iter(rows), handle) == 2
        assert not handle.closed
    with gzip.open(path, "rt", encoding="utf-8") as oracle:
        assert [json.loads(line) for line in oracle] == rows
    with gzip_module.open(path, "rt" if text_mode else "rb", **options) as handle:
        assert ndjson.load(handle) == rows
        assert not handle.closed


@pytest.mark.parametrize("text_mode", [False, True])
def test_typed_ndjson_pipeline_uses_real_gzip_files(
    gzip_module, tmp_path: Path, text_mode
) -> None:
    msgspec = pytest.importorskip("msgspec")
    from unstd.serde import ndjson, structs

    class Row(msgspec.Struct):
        idx: int
        name: str

    codec = structs.Codec(Row)
    rows = [Row(1, "café"), Row(2, "second")]
    path = tmp_path / "typed.jsonl.gz"
    options = {"encoding": "utf-8"} if text_mode else {}
    with gzip_module.open(path, "wt" if text_mode else "wb", **options) as handle:
        assert ndjson.dump(iter(rows), handle, codec=codec) == 2
    with gzip.open(path, "rb") as oracle:
        assert ndjson.load(oracle, codec=codec) == rows
    with gzip_module.open(path, "rt" if text_mode else "rb", **options) as handle:
        assert list(ndjson.iter_loads(handle, codec=codec)) == rows
        assert not handle.closed


def test_base_install_binds_the_exact_stdlib_surface() -> None:
    script = textwrap.dedent(
        """\
        import gzip
        import importlib.abc
        import io
        import sys
        import zlib

        class BlockZlibNg(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "zlib_ng" or fullname.startswith("zlib_ng."):
                    raise ModuleNotFoundError("zlib-ng deliberately unavailable")
                return None

        sys.meta_path.insert(0, BlockZlibNg())
        from unstd.compress import gzipx

        for name in ["open", "GzipFile", "compress", "decompress", "BadGzipFile"]:
            assert getattr(gzipx, name) is getattr(gzip, name), name
        payload = b"first\\nsecond\\n"
        raw = gzipx.compress(payload, mtime=0)
        assert gzip.decompress(raw) == payload
        stream = io.BytesIO()
        with gzipx.open(stream, "wt", encoding="utf-8", newline="\\n") as handle:
            handle.write("café\\n")
        assert not stream.closed
        assert gzip.decompress(stream.getvalue()) == "café\\n".encode()
        malformed = [
            b"not gzip",
            b"\\x1f",
            raw[:-1],
            raw[:-8] + bytes([raw[-8] ^ 1]) + raw[-7:],
            raw[:10] + b"\\x07" + raw[-8:],
        ]
        for data in malformed:
            for module in [gzipx, gzip]:
                for reader in [module.decompress,
                               lambda value: module.GzipFile(fileobj=io.BytesIO(value)).read()]:
                    try:
                        reader(data)
                    except (gzip.BadGzipFile, EOFError, zlib.error):
                        pass
                    else:
                        raise AssertionError("malformed gzip was accepted")
        assert "zlib_ng" not in sys.modules
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed script and current interpreter
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_consumer_typing_distinguishes_binary_and_text_streams(tmp_path: Path) -> None:
    mypy = pytest.importorskip("mypy.api")
    source = tmp_path / "gzip_consumer.py"
    source.write_text(
        textwrap.dedent(
            """\
            import io
            from typing import assert_type
            from unstd.compress import gzipx

            assert_type(gzipx.compress(b"payload"), bytes)
            assert_type(gzipx.decompress(b"payload"), bytes)
            assert_type(gzipx.open("data.gz", "rb"), gzipx.GzipFile)
            assert_type(gzipx.open("data.gz", "wb"), gzipx.GzipFile)
            assert_type(gzipx.open(io.BytesIO(), "rb").read(), bytes)
            assert_type(gzipx.open("data.gz", "rt", encoding="utf-8"), io.TextIOWrapper)
            assert_type(gzipx.open("data.gz", "wt", encoding="utf-8").read(), str)
            """
        ),
        encoding="utf-8",
    )
    options = [
        "--strict",
        "--no-incremental",
        "--follow-imports=silent",
        "--cache-dir",
        str(tmp_path / "mypy-cache"),
        "--no-error-summary",
    ]
    stdout, stderr, status = mypy.run([*options, str(source)])
    assert status == 0, stdout + stderr
    invalid = tmp_path / "invalid_gzip_consumer.py"
    invalid.write_text(
        source.read_text(encoding="utf-8")
        + '\ngzipx.open("data.gz", "wb").write("wrong")\n'
        + 'gzipx.open("data.gz", "wt").write(b"wrong")\n',
        encoding="utf-8",
    )
    stdout, stderr, status = mypy.run([*options, str(invalid)])
    assert status == 1, stdout + stderr
    assert stdout.count("error:") == 2, stdout + stderr
