"""Native gzip edge contracts for inflater state, stream callbacks, and cleanup."""

from __future__ import annotations

import builtins
import gzip
import io
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import TYPE_CHECKING
import zlib

import pytest


if TYPE_CHECKING:
    from pathlib import Path


class _CallbackError(RuntimeError):
    """An application stream failure that must retain its identity."""


@pytest.fixture
def native_backend():
    native = pytest.importorskip("zlib_ng.zlib_ng")
    from unstd.compress import _inflate, gzipx

    return SimpleNamespace(native=native, inflate=_inflate, gzipx=gzipx)


@pytest.mark.parametrize("modern", [False, True])
def test_native_inflater_supports_bounded_output_and_preserves_trailing_bytes(
    native_backend, modern: bool
) -> None:
    payload = b"same row\n" * 80 + bytes(range(256))
    trailer = b"outside the raw DEFLATE member"
    compressed = zlib.compress(payload, wbits=-15) + trailer
    native = native_backend.native
    raw = native._ZlibDecompressor(wbits=-15) if modern else native.decompressobj(-15)
    inflater = native_backend.inflate._Inflater(raw)
    output = []
    pending = compressed
    for _ in range(len(payload) + 1):
        output.append(inflater.decompress(pending, 7))
        if inflater.eof:
            break
        if modern:
            assert not inflater.needs_input
            pending = b""
        else:
            pending = inflater.unconsumed_tail
            assert pending
    else:
        pytest.fail("native inflater made no bounded progress")
    assert b"".join(output) == payload
    assert all(len(chunk) <= 7 for chunk in output)
    assert inflater.unused_data == trailer
    assert inflater.eof


@pytest.mark.parametrize("modern", [False, True])
def test_native_inflater_translates_only_native_codec_errors(
    native_backend, modern: bool
) -> None:
    native = native_backend.native
    raw = native._ZlibDecompressor(wbits=-15) if modern else native.decompressobj(-15)
    inflater = native_backend.inflate._Inflater(raw)
    with pytest.raises(zlib.error) as caught:
        inflater.decompress(b"\x07", 100)
    assert type(caught.value) is zlib.error
    assert isinstance(caught.value.__cause__, native.error)


def test_sequential_sources_allow_short_reads_and_forward_seeks(native_backend) -> None:
    payload = b"first\nsecond\nthird\n" * 100

    class Sequential:
        def __init__(self, data):
            self._data = io.BytesIO(data)
            self.reads = []

        def read(self, size=-1):
            self.reads.append(size)
            return self._data.read(min(size, 3) if size >= 0 else 3)

    raw = gzip.compress(payload, mtime=0)
    source = Sequential(raw)
    with native_backend.gzipx.GzipFile(fileobj=source, mode="rb") as handle:
        assert source.reads == []
        assert handle.read(2) == payload[:2]
        assert handle.seek(8) == 8
        assert handle.read(9) == payload[8:17]
        assert handle.read() == payload[17:]
    assert source.reads


@pytest.mark.parametrize("operation", ["read", "seek", "flush"])
def test_application_stream_failures_propagate_unchanged(
    native_backend, operation: str
) -> None:
    failure = _CallbackError(f"application {operation} failed")

    class Source(io.BytesIO):
        fail_reads = False
        fail_seeks = False

        def read(self, size=-1):
            if self.fail_reads:
                raise failure
            return super().read(size)

        def seek(self, offset, whence=0):
            if self.fail_seeks:
                raise failure
            return super().seek(offset, whence)

    class Sink(io.BytesIO):
        fail_writes = False

        def write(self, data):
            if self.fail_writes:
                raise failure
            return super().write(data)

    module = native_backend.gzipx
    if operation == "flush":
        sink = Sink()
        with module.GzipFile(fileobj=sink, mode="wb") as handle:
            handle.write(b"payload")
            sink.fail_writes = True
            try:
                with pytest.raises(_CallbackError) as caught:
                    handle.flush()
                assert caught.value is failure
            finally:
                sink.fail_writes = False
        assert not sink.closed
    else:
        source = Source(gzip.compress(b"payload"))
        with module.GzipFile(fileobj=source, mode="rb") as handle:
            if operation == "seek":
                handle.read()
                source.fail_seeks = True
            else:
                source.fail_reads = True
            with pytest.raises(_CallbackError) as caught:
                handle.rewind() if operation == "seek" else handle.read()
            assert caught.value is failure
        assert not source.closed


@pytest.mark.parametrize("owned", [False, True])
def test_reader_initialization_failure_closes_only_owned_files(
    native_backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned: bool
) -> None:
    failure = _CallbackError("native reader setup failed")
    path = tmp_path / "payload.gz"
    path.write_bytes(gzip.compress(b"payload"))
    supplied = io.BytesIO(path.read_bytes())
    opened = []
    real_open = builtins.open

    def record_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def fail_reader(_source):
        raise failure

    monkeypatch.setattr(native_backend.gzipx, "_Reader", fail_reader)
    monkeypatch.setattr(builtins, "open", record_open)
    try:
        with pytest.raises(_CallbackError) as caught:
            native_backend.gzipx.GzipFile(
                path if owned else None,
                mode="rb",
                fileobj=None if owned else supplied,
            )
        assert caught.value is failure
        # Keep the traceback alive above: collection must not be the mechanism
        # that eventually closes an owned handle after failed construction.
        assert all(handle.closed for handle in opened)
        assert len(opened) == int(owned)
        assert not supplied.closed
    finally:
        for handle in opened:
            handle.close()
        supplied.close()


def test_invalid_open_inputs_raise_python_errors_without_entering_native_reader() -> (
    None
):
    # zlib-ng 1.0.0 can terminate the interpreter when its native reader receives
    # a non-file object. Keep this regression isolated even if validation breaks.
    script = textwrap.dedent(
        """\
        import gzip
        from unstd.compress import gzipx

        for invalid in [42, object(), None]:
            for module in [gzip, gzipx]:
                try:
                    module.open(invalid, "rb")
                except TypeError:
                    pass
                else:
                    raise AssertionError("invalid gzip filename was accepted")
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed bounded regression script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
