"""Real file handles and fixture-owned failing streams for ``load``/``dump``.

``load`` takes a binary handle and decodes UTF-8 (TOML 1.0.0 requires UTF-8);
``dump`` takes a text handle. Fault streams record each call, so a failure's
identity, the bytes already consumed or written, and later reuse are checked.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


if TYPE_CHECKING:
    from pathlib import Path


DATA = {"name": "unstd", "server": {"port": 8080, "hosts": ["a", "b"]}}
TEXT = 'name = "unstd"\n\n[server]\nport = 8080\nhosts = ["a", "b"]\n'


class FailingReader(io.RawIOBase):
    def __init__(self, error: BaseException) -> None:
        self.error, self.reads = error, 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        raise self.error


class FailingWriter(io.StringIO):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error, self.attempts = error, []

    def write(self, text: str) -> int:
        self.attempts.append(text)
        raise self.error


@pytest.fixture
def tomlkit_backend() -> None:
    require_backend("tomlkit")


def test_load_reads_real_binary_file_including_crlf(tmp_path: Path) -> None:
    path = tmp_path / "cfg.toml"
    path.write_bytes(TEXT.replace("\n", "\r\n").encode())
    with path.open("rb") as handle:
        assert toml.load(handle) == DATA
        assert handle.read() == b""


def test_load_decodes_utf8_and_rejects_other_encodings(tmp_path: Path) -> None:
    path = tmp_path / "cfg.toml"
    path.write_bytes('k = "café"\n'.encode())
    with path.open("rb") as handle:
        assert toml.load(handle) == {"k": "café"}
    path.write_bytes('k = "café"\n'.encode("latin-1"))
    with path.open("rb") as handle, pytest.raises(UnicodeDecodeError):
        toml.load(handle)


def test_load_rejects_text_handles(tmp_path: Path) -> None:
    path = tmp_path / "cfg.toml"
    path.write_text(TEXT, encoding="utf-8")
    with (
        path.open(encoding="utf-8") as handle,
        pytest.raises(TypeError, match="binary mode"),
    ):
        toml.load(handle)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="binary mode"):
        toml.load(io.StringIO(TEXT))  # type: ignore[arg-type]


def test_load_propagates_read_fault_and_stream_stays_usable() -> None:
    error = OSError(5, "injected read fault")
    reader = FailingReader(error)
    with pytest.raises(OSError, match="injected read fault") as caught:
        toml.load(reader)  # type: ignore[arg-type]
    assert caught.value is error
    assert reader.reads == 1
    assert not reader.closed
    assert toml.load(io.BytesIO(TEXT.encode())) == DATA


def test_load_surfaces_malformed_file_as_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "cfg.toml"
    path.write_bytes(b"ok = 1\nbroken = \n")
    with (
        path.open("rb") as handle,
        pytest.raises(toml.TOMLDecodeError, match=r"at line 2"),
    ):
        toml.load(handle)


@pytest.mark.usefixtures("tomlkit_backend")
def test_dump_to_real_text_file_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "out.toml"
    with path.open("w", encoding="utf-8") as handle:
        toml.dump(DATA, handle)
    with path.open("rb") as handle:
        assert toml.load(handle) == DATA


@pytest.mark.usefixtures("tomlkit_backend")
def test_dump_refuses_binary_handle_without_writing() -> None:
    sink = io.BytesIO()
    with pytest.raises(TypeError):
        toml.dump(DATA, sink)  # type: ignore[arg-type]
    assert sink.getvalue() == b""


@pytest.mark.usefixtures("tomlkit_backend")
def test_dump_propagates_write_fault_after_one_complete_attempt() -> None:
    error = OSError(28, "injected disk full")
    sink = FailingWriter(error)
    with pytest.raises(OSError, match="injected disk full") as caught:
        toml.dump(DATA, sink)
    assert caught.value is error
    # The whole document is rendered before the stream is touched.
    assert len(sink.attempts) == 1
    assert toml.loads(sink.attempts[0]) == DATA
    good = io.StringIO()
    toml.dump(DATA, good)
    assert toml.loads(good.getvalue()) == DATA


@pytest.mark.usefixtures("tomlkit_backend")
def test_unrepresentable_value_fails_before_any_write() -> None:
    sink = FailingWriter(AssertionError("stream must not be written"))
    with pytest.raises(TypeError):
        toml.dump({"ok": 1, "bad": None}, sink)
    assert sink.attempts == []


@pytest.mark.usefixtures("tomlkit_backend")
def test_dump_of_edited_document_writes_exact_text() -> None:
    doc = toml.parse("# keep\nport = 1 # inline\n")
    doc["port"] = 2
    sink = io.StringIO()
    toml.dump(doc, sink)
    assert sink.getvalue() == "# keep\nport = 2 # inline\n"
