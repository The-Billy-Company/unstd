"""Codec namespaces preserve whole native modules, types and stream ownership."""

from __future__ import annotations

import importlib
from importlib.util import find_spec
import io
import sys

import pytest

from tests.support.environment.process import run_python
import unstd.compress as codecs


@pytest.mark.parametrize(
    ("provider", "alias", "child", "symbol"),
    [
        ("zipfile", "zipfile", "_path", "Path"),
        (
            "zipfile",
            "zipfile",
            "_path.glob",
            "translate" if sys.version_info < (3, 13) else "Translator",
        ),
        ("zstandard", "zstandard", "backend_c", "ZstdError"),
        ("compression.zstd", "zstd", "_zstdfile", "ZstdFile"),
    ],
)
def test_package_children_keep_original_types_and_import_metadata(
    provider, alias, child, symbol
):
    package = pytest.importorskip(provider)
    if not hasattr(package, "__path__") or find_spec(f"{provider}.{child}") is None:
        pytest.skip("this provider version has no matching child module")
    original = importlib.import_module(f"{provider}.{child}")
    metadata = (
        original.__name__,
        original.__package__,
        original.__spec__,
        original.__loader__,
    )
    importlib.import_module(f"unstd.compress.{alias}")
    pending = find_spec(f"unstd.compress.{alias}.{child}")
    assert pending is not None
    assert (pending.submodule_search_locations is not None) == (
        original.__spec__.submodule_search_locations is not None
    )
    qualified = importlib.import_module(f"unstd.compress.{alias}.{child}")
    assert qualified is original
    assert getattr(qualified, symbol) is getattr(original, symbol)
    assert (
        original.__name__,
        original.__package__,
        original.__spec__,
        original.__loader__,
    ) == metadata
    assert original.__spec__ is metadata[2]
    assert original.__loader__ is metadata[3]
    assert find_spec(f"unstd.compress.{alias}.{child}") is metadata[2]
    if provider == "zstandard":
        with pytest.raises(package.ZstdError):
            qualified.ZstdDecompressor().decompress(b"invalid frame")
        assert package.backend_c.ZstdError is package.ZstdError
    elif provider == "zipfile" and child == "_path":
        assert package.Path is package._path.Path


def test_package_alias_does_not_load_an_unused_cffi_backend(tmp_path):
    pytest.importorskip("zstandard.backend_c")
    result = run_python(
        """
import importlib.abc
import sys
class NoCffi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'cffi', '_cffi_backend', 'zstandard.backend_cffi'}:
            raise AssertionError('unused optional backend loaded: ' + fullname)
sys.meta_path.insert(0, NoCffi())
from unstd.compress import zstandard
import zstandard as original
assert zstandard is original
assert 'zstandard.backend_cffi' not in sys.modules
from unstd.compress.zstandard.backend_c import ZstdError
assert ZstdError is original.ZstdError
try:
    import unstd.compress.zstandard.no_such_backend
except ModuleNotFoundError as error:
    assert error.name == 'unstd.compress.zstandard.no_such_backend'
else:
    raise AssertionError('missing provider child was executed under its alias')
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["bz2", "lzma", "tarfile", "zipfile", "zlib"])
def test_package_and_qualified_imports_return_the_original_module(name):
    qualified = importlib.import_module(f"unstd.compress.{name}")
    original = importlib.import_module(name)
    assert qualified is original
    assert getattr(codecs, name) is original
    assert sys.modules[f"unstd.compress.{name}"] is original


def test_package_import_and_directory_inspection_load_no_codec(tmp_path):
    result = run_python(
        """
import importlib.abc
import sys
blocked = {'bz2', 'lzma', 'tarfile', 'zipfile', 'zlib', 'zstandard', 'compression', 'zlib_ng'}
for name in list(sys.modules):
    if name.split('.')[0] in blocked:
        del sys.modules[name]
class NoCodecs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('eager codec import: ' + fullname)
sys.meta_path.insert(0, NoCodecs())
import unstd.compress as codecs
assert {'bz2', 'lzma', 'tarfile', 'zipfile', 'zlib', 'zstd', 'zstandard'} <= set(dir(codecs))
assert not any(name.split('.')[0] in blocked for name in sys.modules)
assert 'unstd.compress.engines.imports' not in sys.modules
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_absent_native_zstandard_names_its_extra_without_api_fallback(tmp_path):
    result = run_python(
        """
import importlib.abc
import sys
for name in list(sys.modules):
    if name == 'zstandard' or name.startswith('unstd.compress'):
        del sys.modules[name]
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'zstandard':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Missing())
import unstd.compress as codecs
assert 'zstandard' not in vars(codecs)
try:
    from unstd.compress.zstandard import ZstdCompressor
except ImportError as error:
    assert "unstd[zstd]" in str(error)
else:
    raise AssertionError('optional backend silently selected another API')
assert 'zstandard' not in vars(codecs)
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_native_zstandard_preserves_dictionary_frames_limits_and_reader_ownership():
    original = pytest.importorskip("zstandard")
    from unstd.compress.zstandard import ZstdCompressor, ZstdError

    assert codecs.zstandard is original
    assert ZstdCompressor is original.ZstdCompressor
    assert ZstdError is original.ZstdError
    samples = [
        f"row={i} category={i % 7} café 東京 field=data source".encode()
        for i in range(200)
    ]
    dictionary = original.train_dictionary(512, samples)
    raw = b"\n".join(samples)
    encoder = codecs.zstandard.ZstdCompressor(dict_data=dictionary)
    wire = encoder.compress(raw)
    parameters = codecs.zstandard.get_frame_parameters(wire)
    assert parameters.dict_id == dictionary.dict_id()
    assert parameters.content_size == len(raw)
    decoder = codecs.zstandard.ZstdDecompressor(dict_data=dictionary)
    assert (
        decoder.decompress(wire, max_output_size=len(raw), allow_extra_data=False)
        == raw
    )
    with pytest.raises(original.ZstdError):
        decoder.decompress(wire + b"trailing", allow_extra_data=False)
    joined = wire + wire
    state = decoder.decompressobj()
    assert state.decompress(joined) == raw
    assert state.eof
    assert state.unused_data == wire
    source = io.BytesIO(wire)
    with decoder.stream_reader(source, closefd=False) as reader:
        assert reader.read(17) + reader.read() == raw
    assert not source.closed


@pytest.mark.skipif(sys.version_info < (3, 14), reason="stdlib zstd starts in3.14")
def test_stdlib_zstd_is_distinct_from_native_backend_and_preserves_text_streams():
    from compression import zstd

    from unstd.compress.zstd import ZstdCompressor

    assert codecs.zstd is zstd
    assert ZstdCompressor is zstd.ZstdCompressor
    stream = io.BytesIO()
    with codecs.zstd.open(stream, "wt", encoding="utf-8", newline="") as writer:
        writer.write("first café\nsecond 東京\n")
    assert not stream.closed
    stream.seek(0)
    with codecs.zstd.open(stream, "rt", encoding="utf-8") as reader:
        assert list(reader) == ["first café\n", "second 東京\n"]
    assert not stream.closed


def test_old_interpreters_refuse_the_stdlib_zstd_namespace_explicitly(tmp_path):
    result = run_python(
        """
import importlib
import sys
import unstd.compress as codecs
sys.version_info = (3, 13, 0)
try:
    importlib.import_module('unstd.compress.zstd')
except ImportError as error:
    assert 'Python 3.14' in str(error)
else:
    raise AssertionError('incompatible stdlib zstd namespace selected')
assert 'zstd' not in vars(codecs)
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["bz2", "lzma"])
def test_native_bz2_and_lzma_keep_bounded_state_and_borrowed_text_files(name):
    codec = getattr(codecs, name)
    raw = b"source bytes" * 1000
    packed = codec.compress(raw)
    decoder = codec.BZ2Decompressor() if name == "bz2" else codec.LZMADecompressor()
    output = [decoder.decompress(packed + b"tail", max_length=17)]
    assert len(output[0]) == 17
    while not decoder.eof:
        assert not decoder.needs_input
        output.append(decoder.decompress(b"", max_length=31))
    assert b"".join(output) == raw
    assert decoder.unused_data == b"tail"
    target = io.BytesIO()
    with codec.open(target, "wt", encoding="utf-8") as writer:
        writer.write("café 東京\n")
    assert not target.closed
    target.seek(0)
    with codec.open(target, "rt", encoding="utf-8") as reader:
        assert reader.read() == "café 東京\n"
    assert not target.closed


def test_stock_zlib_keeps_exact_deflate_and_compressor_copy_contracts():
    import zlib

    from unstd.compress.zlib import compressobj, crc32

    assert compressobj is zlib.compressobj
    assert crc32 is zlib.crc32
    raw = bytes(range(256)) * 1024
    stock = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    port = compressobj(9, codecs.zlib.DEFLATED, -codecs.zlib.MAX_WBITS)
    assert port.compress(raw) + port.flush() == stock.compress(raw) + stock.flush()
    prefix = compressobj(level=6, wbits=31)
    head = prefix.compress(raw[:65536])
    checkpoint = prefix.copy()
    assert head + checkpoint.compress(
        memoryview(raw)[65536:]
    ) + checkpoint.flush() == codecs.zlib.compress(raw, level=6, wbits=31)


def test_zip_and_tar_namespaces_preserve_archive_classes_and_metadata():
    import tarfile
    import zipfile

    from unstd.compress.zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

    assert codecs.tarfile.TarFile is tarfile.TarFile
    assert codecs.tarfile.TarInfo is tarfile.TarInfo
    assert codecs.zipfile.BadZipFile is zipfile.BadZipFile
    assert ZipInfo is zipfile.ZipInfo
    target = io.BytesIO()
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        metadata = ZipInfo("folder/東京.txt", date_time=(2020, 1, 1, 0, 0, 0))
        metadata.external_attr = 0o644 << 16
        archive.writestr(
            metadata, b"source", compress_type=ZIP_DEFLATED, compresslevel=1
        )
    assert not target.closed
    with ZipFile(target) as archive:
        metadata = archive.infolist()[0]
        assert metadata.filename == "folder/東京.txt"
        assert metadata.file_size == 6
        assert metadata.compress_type == ZIP_DEFLATED
        assert metadata.external_attr == 0o644 << 16
        with archive.open(metadata) as member:
            assert member.read(3) + member.read() == b"source"


def test_unknown_namespace_does_not_import_an_unrelated_provider():
    with pytest.raises(AttributeError, match="has no attribute"):
        codecs.__getattr__("unknown_codec")
