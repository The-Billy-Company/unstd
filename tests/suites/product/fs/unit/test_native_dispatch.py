"""Admission keeps the public filesystem contract and its Python fallback."""

from __future__ import annotations

import errno
import os
from types import SimpleNamespace

import pytest

from unstd import fs


@pytest.mark.parametrize("payload", [b"binary\x00\xff", "héllo 雪\n"])
def test_public_dispatch_preserves_payload_type_and_path(
    tmp_path, monkeypatch, payload
):
    reached = []

    def native(path, data, *, durable):
        reached.append((path, data, durable))
        fs._atomic_write_python(path, data, "utf-8", durable)

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=native))
    path = tmp_path / "state"
    assert fs.atomic_write(path, payload, durable=True) == path
    assert reached == [(path, payload, True)]
    assert type(reached[0][1]) is type(payload)


def test_declined_native_work_uses_complete_python_parent_preparation(
    tmp_path, monkeypatch
):
    reached = []

    def native(*args, **kwargs):
        reached.append((args, kwargs))
        return NotImplemented

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=native))
    path = tmp_path / "new" / "parents" / "state"
    assert fs.atomic_write(path, b"complete") == path
    assert len(reached) == 1
    assert path.read_bytes() == b"complete"


def test_native_io_error_never_retries_the_write_through_python(tmp_path, monkeypatch):
    path = tmp_path / "state"
    path.write_bytes(b"previous")
    error = OSError(errno.EIO, "native operation failed")

    def native(*_args, **_kwargs):
        raise error

    def forbidden(*_args, **_kwargs):
        pytest.fail("an I/O failure retried through Python")

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=native))
    monkeypatch.setattr(fs, "_atomic_write_python", forbidden)
    with pytest.raises(OSError) as caught:  # noqa: PT011 — original identity below
        fs.atomic_write(path, b"new")
    assert caught.value is error
    assert path.read_bytes() == b"previous"


@pytest.mark.parametrize("encoding", ["latin-1", "utf8", "missing-codec"])
def test_other_codecs_retain_python_text_validation_and_cleanup(
    tmp_path, monkeypatch, encoding
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("noncanonical text encoding reached native code")

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=forbidden))
    path = tmp_path / "new" / "state"
    if encoding == "missing-codec":
        with pytest.raises(LookupError):
            fs.atomic_write(path, "café", encoding=encoding)
        assert path.parent.is_dir()
        assert list(path.parent.iterdir()) == []
    else:
        assert fs.atomic_write(path, "café", encoding=encoding) == path
        assert path.read_bytes() == "café".encode(encoding)


def test_text_subclass_keeps_stream_behavior_without_calling_encode(
    tmp_path, monkeypatch
):
    class Text(str):
        def encode(self, *_args, **_kwargs):
            pytest.fail("str subclass encode override was called")

    def forbidden(*_args, **_kwargs):
        pytest.fail("subclass reached native code")

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=forbidden))
    path = tmp_path / "state"
    fs.atomic_write(path, Text("héllo"))
    assert path.read_text() == "héllo"


def test_custom_os_binding_remains_observable_on_python_path(tmp_path, monkeypatch):
    synced = []

    class Boundary:
        def __getattr__(self, name):
            return getattr(os, name)

        def fsync(self, fd):
            synced.append(fd)
            os.fsync(fd)

    def forbidden(*_args, **_kwargs):
        pytest.fail("custom OS binding was bypassed")

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=forbidden))
    monkeypatch.setattr(fs, "os", Boundary())
    path = tmp_path / "state"
    fs.atomic_write(path, b"complete")
    assert len(synced) == 1
    assert path.read_bytes() == b"complete"


def test_missing_native_backend_retains_public_text_and_binary_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(fs, "_native", None)
    path = tmp_path / "state"
    for data in (b"binary", "héllo"):
        assert fs.atomic_write(path, data) == path
        assert path.read_bytes() == (data if isinstance(data, bytes) else data.encode())


def test_callable_equality_cannot_impersonate_the_original_os_binding(
    tmp_path, monkeypatch
):
    opened = []
    real_open = os.open

    class OpenProxy:
        def __call__(self, *args, **kwargs):
            opened.append(args[0])
            return real_open(*args, **kwargs)

        def __eq__(self, _other):
            pytest.fail("hook equality must not run during admission")

    def forbidden(*_args, **_kwargs):
        pytest.fail("a custom callable was bypassed by native dispatch")

    monkeypatch.setattr(fs, "_native", SimpleNamespace(atomic_write=forbidden))
    monkeypatch.setattr(os, "open", OpenProxy())
    path = tmp_path / "state"
    fs.atomic_write(path, b"complete")
    assert len(opened) == 1
    assert path.read_bytes() == b"complete"
