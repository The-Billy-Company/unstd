"""Downstream strict typing resolves the installed wheel, including rejection controls."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from tests.support.packaging.artifacts import (
    Consumer,
    checked_command,
    create_consumer,
    install_extra_closure,
)


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.packaging.artifacts import Artifacts


_POSITIVE = """from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
import sys
from typing import Literal, assert_type
from unstd import clone, fs, proc, rand, rex
from unstd.compress import bz2, lzma, tarfile, zipfile, zlib, zlibx
from unstd.ids import uid
from unstd.pack import binpack
from unstd.pack.ragged import RaggedDecoder
from unstd.serde import b64, jsonx
from unstd.time import timeutil

assert_type(b64.encode(b'wire'), str)
assert_type(b64.decode('d2lyZQ=='), bytes)
assert_type(jsonx.dumps({'value': 1}), str)
assert_type(jsonx.dumpb({'value': 1}), bytes)
assert_type(jsonx.canonical_strict({'value': 1}), bytes)
assert_type(binpack.pack_array_portable('i4', [1, 2]), bytes)
assert_type(binpack.unpack_array_portable('i4', b''), list[float])
assert_type(binpack.unpack_array_portable('i4', b'prefix', count=0, offset=6), list[float])
decoder = RaggedDecoder('<f4', columns=2, index_dtype='<i4', finite=True, allow_empty=False)
assert_type(decoder.dtype, str)
assert_type(decoder.columns, int)
assert_type(decoder.index_dtype, str)
assert_type(decoder.finite, bool)
assert_type(decoder.allow_empty, bool)
assert_type(rand.Generator(173).random(), float)
assert_type(rand.Generator(173).backend, Literal['stdlib', 'numpy'])
seeds: Sequence[int] = [173, 179]
rand.ints(2, 0, 10, seeds=seeds)
rand.Generator(173).ints(2, 0, 10, endpoint=True, seeds=seeds)
assert_type(rex.compile_linear('[a-z]+').requires_ascii, bool)
assert_type(uid.new(), str)
assert_type(uid.new_hex(), str)
assert_type(clone.deep([1, 2]), list[int])
assert_type(fs.atomic_write(Path('output'), 'value'), Path)
assert_type(fs.atomic_write(Path('output'), 'value', durable='strict'), Path)
assert_type(proc.capture(['program']), str)
assert_type(timeutil.parse_utc('2026-01-01T00:00:00Z'), datetime | None)
assert_type(bz2.compress(b'wire'), bytes)
assert_type(lzma.decompress(b'wire'), bytes)
assert_type(zlib.compressobj().compress(b'wire'), bytes)
assert_type(zlibx.compressobj().copy().flush(), bytes)
assert_type(zlibx.decompressobj().unused_data, bytes)
assert_type(zipfile.ZipFile('archive.zip').namelist(), list[str])
assert_type(tarfile.TarInfo('entry').size, int)
if sys.version_info >= (3, 14):
    from unstd.compress import zstd
    assert_type(zstd.decompress(b'wire'), bytes)
"""

_NEGATIVE = """from unstd.ids import uid
from unstd.serde import b64, jsonx
from unstd import clone, fs, rand
from unstd.compress import bz2, zlib, zlibx
from unstd.pack.ragged import RaggedDecoder

wrong_bytes: bytes = b64.encode(b'wire')
wrong_text: str = b64.decode('d2lyZQ==')
wrong_json: bytes = jsonx.dumps({'value': 1})
wrong_id: int = uid.new()
original: list[int] = [1, 2]
wrong_clone: list[str] = clone.deep(original)
fs.atomic_write('output', 'value', durable='invalid')
rand.Generator(173, backend='auto')
wrong_bz2: str = bz2.compress(b'wire')
wrong_zlib: int = zlib.compressobj().compress(b'wire')
wrong_zlibx: str = zlibx.decompressobj().unused_data
RaggedDecoder('<f4', columns='two')
RaggedDecoder('<f4', columns=2).unpack(b'', index_count='two')
wrong_ragged: str = RaggedDecoder('<f4', columns=2).unpack(b'', index_count=1)
"""


def _mypy_command(consumer: Consumer) -> list[str | Path]:
    config = consumer.root / "consumer-mypy.ini"
    config.write_text("[mypy]\n", encoding="utf-8")
    return [
        sys.executable,
        "-I",
        "-m",
        "mypy",
        "--config-file",
        config,
        "--python-executable",
        consumer.python,
        "--no-incremental",
        "--strict",
        "--ignore-missing-imports",
        "--follow-imports=silent",
        "--no-error-summary",
        "--show-error-codes",
        "--no-pretty",
    ]


def test_downstream_strict_mypy_consumes_installed_types_with_negative_controls(
    consumer: Consumer,
) -> None:
    positive = consumer.root / "typed_consumer.py"
    positive.write_text(_POSITIVE, encoding="utf-8")
    negative = consumer.root / "invalid_consumer.py"
    negative.write_text(_NEGATIVE, encoding="utf-8")
    common = _mypy_command(consumer)
    result = checked_command([*common, positive], cwd=consumer.root)
    assert result.stdout == result.stderr == ""
    rejected = checked_command([*common, negative], cwd=consumer.root, expected_code=1)
    errors = [line for line in rejected.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 13, rejected.stdout
    assert all("[assignment]" in line for line in errors[:4]), rejected.stdout
    assert all("[arg-type]" in line for line in errors[4:7]), rejected.stdout
    assert all("[assignment]" in line for line in errors[7:10]), rejected.stdout
    assert all("[arg-type]" in line for line in errors[10:12]), rejected.stdout
    assert "[assignment]" in errors[12], rejected.stdout
    assert not rejected.stderr


_COMPRESS_EXTRA_TYPES = {
    "zstd": (
        """from typing import assert_type
from unstd.compress import zstandard
from unstd.compress.zstandard import ZstdCompressor, ZstdDecompressor

payload = b'installed typed source'
dictionary = zstandard.ZstdCompressionDict(payload, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
wire = ZstdCompressor(dict_data=dictionary).compress(payload)
assert_type(wire, bytes)
assert_type(ZstdDecompressor(dict_data=dictionary).decompress(wire, max_output_size=1024), bytes)
assert_type(zstandard.get_frame_parameters(wire).content_size, int)
""",
        """from unstd.compress.zstandard import ZstdCompressor
wrong: str = ZstdCompressor().compress(b'wire')
""",
    ),
    "archives": (
        """from typing import assert_type
from unstd.compress.libarchive import ffi
from unstd.compress.libarchive.entry import ArchiveEntry
from unstd.compress.libarchive.exception import ArchiveError
from unstd.compress.libarchive.read import ArchiveRead, new_archive_read

def read(data: bytes) -> list[bytes]:
    result: list[bytes] = []
    try:
        with new_archive_read() as pointer:
            ffi.get_read_format_function('raw')(pointer)
            ffi.read_open_memory(pointer, data, len(data))
            archive = ArchiveRead(pointer)
            assert_type(ffi.error_string(archive._pointer), bytes | None)
            for entry in archive:
                assert_type(entry, ArchiveEntry)
                assert_type(entry.size, int | None)
                assert_type(entry.isreg, bool)
                result.append(b''.join(entry.get_blocks(8192)))
    except ArchiveError as error:
        raise ValueError('source archive') from error
    return result
""",
        """from unstd.compress.libarchive.entry import ArchiveEntry
from unstd.compress.libarchive import ffi
wrong_entry: str = ArchiveEntry().isreg
wrong_diagnostic: int = ffi.error_string(1)
""",
    ),
}


@pytest.mark.parametrize("extra", _COMPRESS_EXTRA_TYPES)
def test_installed_compression_extra_keeps_required_typed_boundaries(
    extra: str,
    pytestconfig: pytest.Config,
    artifacts: Artifacts,
    wheel: Path,
    tmp_path: Path,
) -> None:
    if not pytestconfig.getoption("--require-extras"):
        pytest.skip("isolated optional-backend typing requires --require-extras")
    consumer = create_consumer(tmp_path / f"typed-{extra}-consumer", wheel)
    install_extra_closure(artifacts, consumer, extra)
    source, invalid = _COMPRESS_EXTRA_TYPES[extra]
    positive = consumer.root / "typed_compression.py"
    positive.write_text(source, encoding="utf-8")
    negative = consumer.root / "invalid_compression.py"
    negative.write_text(invalid, encoding="utf-8")
    common = _mypy_command(consumer)
    result = checked_command([*common, positive], cwd=consumer.root)
    assert result.stdout == result.stderr == ""
    rejected = checked_command([*common, negative], cwd=consumer.root, expected_code=1)
    errors = [line for line in rejected.stdout.splitlines() if ": error:" in line]
    assert len(errors) == (2 if extra == "archives" else 1), rejected.stdout
    assert all("[assignment]" in line for line in errors), rejected.stdout
    assert not rejected.stderr
