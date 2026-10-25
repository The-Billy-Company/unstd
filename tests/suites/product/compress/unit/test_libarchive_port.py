"""Original archive modules keep one native FFI and its reader/error lifetimes."""

from __future__ import annotations

import gzip
import importlib
from importlib.util import find_spec
import io
import sys
import tarfile

import pytest

from tests.support.environment.process import run_python


@pytest.fixture
def archive_backend():
    if find_spec("libarchive") is None:
        pytest.skip("optional libarchive-c binding is unavailable")
    try:
        return importlib.import_module("unstd.compress.libarchive")
    except ImportError:
        if sys.platform == "win32":
            pytest.skip("Windows requires a separately installed native libarchive DLL")
        raise


def test_archive_package_and_qualified_children_are_original_singletons(
    archive_backend,
):
    provider = importlib.import_module("libarchive")
    assert archive_backend is provider
    assert archive_backend.__all__ == provider.__all__
    for name in ("ffi", "read", "write", "entry", "exception", "extract", "flags"):
        qualified = importlib.import_module(f"unstd.compress.libarchive.{name}")
        original = importlib.import_module(f"libarchive.{name}")
        assert qualified is original
        assert qualified.__name__ == f"libarchive.{name}"
        assert sys.modules[f"unstd.compress.libarchive.{name}"] is original
    from libarchive.read import ArchiveRead as NativeRead
    from libarchive.read import new_archive_read as native_context

    from unstd.compress.libarchive.read import ArchiveRead, new_archive_read

    assert ArchiveRead is NativeRead
    assert new_archive_read is native_context
    assert archive_backend.ArchiveError is provider.exception.ArchiveError
    assert archive_backend.ArchiveEntry is provider.entry.ArchiveEntry
    assert archive_backend.file_writer is provider.write.file_writer


def test_archive_module_and_directory_listing_do_not_load_a_native_library(tmp_path):
    result = run_python(
        """
import importlib.abc
import sys
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'libarchive' or fullname.startswith('libarchive.'):
            raise AssertionError('eager archive provider load: ' + fullname)
sys.meta_path.insert(0, Missing())
import unstd.compress as codecs
assert 'libarchive' in dir(codecs)
assert not any(name.startswith('libarchive') for name in sys.modules)
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_missing_archive_binding_explains_the_optional_extra(tmp_path):
    result = run_python(
        """
import importlib.abc
import sys
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'libarchive':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Missing())
try:
    from unstd.compress.libarchive.read import ArchiveRead
except ImportError as error:
    assert 'unstd[archives]' in str(error)
    assert 'native libarchive' in str(error)
else:
    raise AssertionError('missing archive provider silently fell back')
""",
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fault", ["missing-library", "missing-symbol"])
def test_missing_native_library_is_reported_only_on_explicit_import(tmp_path, fault):
    if find_spec("libarchive") is None:
        pytest.skip("optional libarchive-c binding is unavailable")
    result = run_python(
        """
import ctypes
import sys
import unstd.compress as codecs
assert 'libarchive' not in vars(codecs)
def missing(_path):
    if sys.argv[1] == 'missing-symbol':
        return object()
    raise OSError('native archive DSO unavailable')
ctypes.cdll.LoadLibrary = missing
try:
    from unstd.compress.libarchive import ffi
except ImportError as error:
    assert 'unstd[archives]' in str(error)
    assert isinstance(error.__cause__, (OSError, AttributeError))
else:
    raise AssertionError('missing native archive library was hidden')
""",
        args=(fault,),
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_native_gzip_raw_filter_retains_bounded_blocks_and_diagnostics(archive_backend):
    from unstd.compress.libarchive import ffi
    from unstd.compress.libarchive.read import ArchiveRead, new_archive_read

    raw = "original archive bytes café 東京".encode() * 100
    packed = gzip.compress(raw, mtime=0)
    with new_archive_read() as pointer:
        ffi.get_read_format_function("raw")(pointer)
        ffi.read_open_memory(pointer, packed, len(packed))
        reader = ArchiveRead(pointer)
        entries = 0
        for entry in reader:
            chunks = list(entry.get_blocks(31))
            assert all(len(block) <= 31 for block in chunks)
            assert b"".join(chunks) == raw
            assert entry.size is None
            assert entry.isreg
            entries += 1
        assert entries == 1
        assert reader.format_name == b"raw"
        assert b"gzip" in reader.filter_names
        assert ffi.error_string(pointer) is None
    assert ffi.error_string is archive_backend.ffi.error_string


def test_native_tar_headers_and_entry_contents_use_the_same_provider(archive_backend):
    target = io.BytesIO()
    entries = [("notes/東京.txt", b"one"), ("empty", b"")]
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        for name, raw in entries:
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
    with archive_backend.memory_reader(target.getvalue()) as archive:
        actual = [(entry.pathname, b"".join(entry.get_blocks())) for entry in archive]
    assert actual == entries


def test_native_format_errors_keep_the_original_exception_and_live_diagnostic(
    archive_backend,
):
    from unstd.compress.libarchive import ffi
    from unstd.compress.libarchive.exception import ArchiveError
    from unstd.compress.libarchive.read import ArchiveRead, new_archive_read

    with new_archive_read() as pointer:

        def read_invalid():
            raw = b"not an archive"
            ffi.read_open_memory(pointer, raw, len(raw))
            next(iter(ArchiveRead(pointer)))

        with pytest.raises(ArchiveError) as caught:
            read_invalid()
        assert type(caught.value) is archive_backend.ArchiveError
        assert caught.value.archive_p == pointer
        assert ffi.error_string(pointer)


def test_existing_warning_and_error_callback_identity_is_preserved(
    archive_backend, monkeypatch, caplog
):
    from libarchive import ffi as provider

    from unstd.compress.libarchive import ffi
    from unstd.compress.libarchive.exception import ArchiveError
    from unstd.compress.libarchive.read import new_archive_read

    assert ffi is provider
    assert ffi.read_open_memory.errcheck is provider.check_int
    assert ffi.read_open_memory.errcheck is ffi.check_int
    assert ArchiveError is provider.ArchiveError
    monkeypatch.setattr(
        provider, "error_string", lambda _pointer: b"retained native diagnostic"
    )
    with new_archive_read() as pointer:
        with caplog.at_level("WARNING", logger="libarchive"):
            assert (
                ffi.check_int(ffi.ARCHIVE_WARN, ffi.read_open_memory, [pointer])
                == ffi.ARCHIVE_WARN
            )
        assert "retained native diagnostic" in caplog.text
        failure = ffi.archive_error(pointer, ffi.ARCHIVE_FATAL)
        assert type(failure) is ArchiveError
        assert failure.msg == "retained native diagnostic"
