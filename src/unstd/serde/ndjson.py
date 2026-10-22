r"""NDJSON — newline-delimited JSON, one JSON value per line.

NDJSON (aka JSON-lines / ``.jsonl``) is the append-friendly, stream-friendly
sibling of a JSON array: ``\\n``-separated JSON values, each independently
parseable. It is what trace exports, eval datasets, log shipping, and machine
learning corpora are written as — large files you want to append to and read a
line at a time without materializing the whole array.

This module is a thin layer over :mod:`jsonx` so the whole ``serde`` package
rides one JSON backend — never re-import orjson here. Each record is encoded with
``jsonx.dumpb`` (compact UTF-8) and decoded with ``jsonx.loads``,
so NDJSON inherits jsonx's backend + stdlib fallback transparently.

Pass a reusable ``structs.Codec(T)`` as ``codec=`` to decode each row directly
into a validated ``T`` or write typed records with that codec. Typed readers
require the ``serde`` extra. ``on_error`` observes each rejected row once before
the default raise or an explicit ``skip_errors=True``.

Conventions:

- **Trailing newline per record.** ``dumps``/``dumpb``/``dump`` terminate
  *every* line — including the last — so the output is append-safe (a following
  writer starts on a fresh line) and matches the ``json.dumps(x) + "\\n"``
  idiom the migrated call-sites produced.
- **Blank lines are skipped** on read (interior *and* trailing), so a file with
  a trailing newline round-trips cleanly.
- **Malformed lines are strict by default** — ``iter_loads``/``loads``/``load``
  raise :class:`NDJSONDecodeError` tagged with the 1-based line number. Pass
  ``skip_errors=True`` for the lenient "drop the bad line, keep the rest" mode
  some tolerant readers want.
"""

from __future__ import annotations

import errno
import io
from typing import TYPE_CHECKING, Protocol, overload

from unstd._extra import missing
from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from unstd.serde.jsonx import Json

__all__ = [
    "NDJSONDecodeError",
    "dump",
    "dumpb",
    "dumps",
    "iter_loads",
    "load",
    "loads",
]


# Untyped readers keep jsonx's faithful decoder, including integer precision,
# non-finite constants, Unicode support and interpreter limits.
_parse = jsonx.loads
_line = jsonx._line
_loads_lines: Callable[[object], object] | None = getattr(
    getattr(jsonx, "_native", None), "loads_lines", None
)


class _MethodCodec[T](Protocol):
    """A codec exposing ordinary bound encode/decode methods."""

    def encode(self, value: T, /) -> bytes:
        """One record as JSON bytes, without a trailing newline."""
        ...

    def decode(self, raw: str | bytes, /) -> T:
        """One decoded record, raising msgspec decode errors on invalid input."""
        ...


class _CallableCodec[T](Protocol):
    """The paired callable attributes bound by ``structs.Codec[T]``."""

    encode: Callable[[T], bytes]
    decode: Callable[[str | bytes], T]


# mypy 1.15 treats Callable attributes as descriptors during method-protocol
# matching. Keep both structural forms so the supported typing floor accepts
# existing bound-callable codecs and codecs implemented with ordinary methods.
type _Codec[T] = _MethodCodec[T] | _CallableCodec[T]


class _Writer(Protocol):
    """A binary sink, including gzip streams and write-only destinations."""

    def write(self, data: bytes, /) -> object:
        """Write a prefix and return its count, or acknowledge a full write with None."""
        ...


class NDJSONDecodeError(ValueError):
    """A line failed to parse — carries its 1-based ``lineno`` for context."""

    def __init__(self, lineno: int, err: Exception) -> None:
        """Initialize the instance."""
        self.lineno = lineno
        self.err = err
        super().__init__(f"line {lineno}: {err}")


def _encoder[T](codec: _Codec[T] | None) -> Callable[[T], bytes]:
    """Bind the newline-terminated encoder once per stream."""
    if codec is None:
        return _line
    encode = codec.encode

    def line(row: T) -> bytes:
        return encode(row) + b"\n"

    return line


def _write_all[T: (str, bytes)](
    write: Callable[[T], object], data: T, *, require_count: bool
) -> None:
    """Finish a record before consuming another, without spinning on a full sink."""
    while data:
        written = write(data)
        if written is None and not require_count:
            # Generic write-only sinks, including codecs.StreamWriter, may
            # acknowledge a successful whole write without returning a count.
            return
        if written is None or written == 0:
            msg = "NDJSON sink made no write progress"
            raise BlockingIOError(errno.EAGAIN, msg)
        if not isinstance(written, int) or not 0 < written <= len(data):
            msg = "NDJSON sink returned an invalid write count"
            raise OSError(msg)
        data = data[written:]


def dumps[T](rows: Iterable[T], *, codec: _Codec[T] | None = None) -> str:
    """Serialize *rows* to an NDJSON ``str`` — one compact, newline-terminated line each."""
    return dumpb(rows, codec=codec).decode()


def dumpb[T](rows: Iterable[T], *, codec: _Codec[T] | None = None) -> bytes:
    """Serialize *rows* straight to NDJSON ``bytes`` for file/socket writes."""
    if codec is None:
        return b"".join(map(_line, rows))
    encode = codec.encode

    def line(row: T) -> bytes:
        return encode(row) + b"\n"

    return b"".join(map(line, rows))


@overload
def iter_loads[T](
    lines: Iterable[str | bytes],
    *,
    codec: _Codec[T],
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> Iterator[T]: ...


@overload
def iter_loads(
    lines: Iterable[str | bytes],
    *,
    codec: None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> Iterator[Json]: ...


def iter_loads[T](
    lines: Iterable[str | bytes],
    *,
    codec: _Codec[T] | None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> Iterator[Json | T]:
    """Stream-parse *lines* (a file object, ``splitlines()``, any line iterable) lazily.

    Consumes and yields one line at a time — never materializes the whole input,
    the property that matters for large trace/eval files. Blank / whitespace-only
    lines are skipped. A malformed line raises :class:`NDJSONDecodeError` with its
    1-based line number. ``codec=`` validates into its schema directly. The error
    retains the backend's exception and field path in ``err``. ``on_error`` runs
    once before the error is raised or skipped; its own exceptions propagate.
    """
    decode: Callable[[str | bytes], Json | T] = _parse
    errors: tuple[type[Exception], ...] = (ValueError, RecursionError)
    if codec is not None:
        try:
            from msgspec import DecodeError
        except ImportError:
            missing("serde.ndjson.iter_loads", "serde")()
        decode = codec.decode
        errors = (DecodeError,)
    for lineno, line in enumerate(lines, 1):
        if not line or line.isspace():  # exactly the lines `strip()` empties, no copy
            continue
        if codec is not None:
            # Normalize before a typed decode: retrying a schema failure could
            # run a caller's conversion hook twice for the same record.
            line = line.strip()
        try:
            value = decode(line)
        except errors as original:
            cause = original
            if codec is None and (stripped := line.strip()) != line:
                try:
                    value = decode(stripped)
                except errors as stripped_error:
                    cause = stripped_error
                else:
                    yield value
                    continue
            error = NDJSONDecodeError(lineno, cause)
            if on_error is not None:
                on_error(error)
            if skip_errors:
                continue
            raise error from cause
        yield value


@overload
def loads[T](
    text: str | bytes,
    *,
    codec: _Codec[T],
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[T]: ...


@overload
def loads(
    text: str | bytes,
    *,
    codec: None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[Json]: ...


def loads[T](
    text: str | bytes,
    *,
    codec: _Codec[T] | None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[Json] | list[T]:
    r"""Parse a whole NDJSON blob into a ``list`` — blank lines skipped, trailing newline tolerated.

    Splits strictly on ``\\n`` (not ``str.splitlines``, which also breaks on
    ``\\r``, U+0085 NEL, U+2028/U+2029, VT/FF): those code points occur *inside*
    JSON string bodies (scraped web text, LLM output) where orjson leaves them
    unescaped, and over-splitting there silently corrupts a record. A ``\\r\\n``
    writer is still tolerated — :func:`iter_loads` strips the trailing ``\\r``.
    """
    if (
        codec is None
        and skip_errors is False
        and on_error is None
        and _loads_lines is not None
        and jsonx._HAVE_MSGSPEC is True
        and _parse is jsonx.loads
    ):
        result = _loads_lines(text)
        if isinstance(result, list):
            return result
    lines = (
        text.split(b"\n") if isinstance(text, bytes | bytearray) else text.split("\n")
    )
    if codec is None and not skip_errors and on_error is None:
        # One comprehension over the backend decoder is the whole cost of a clean
        # blob. A failure re-walks through `iter_loads`, which strips exotic
        # padding and names the line — so the answer, and the error, are its.
        try:
            return [_parse(line) for line in lines if line and not line.isspace()]
        except (ValueError, RecursionError):
            pass
    if codec is None:
        return list(iter_loads(lines, skip_errors=skip_errors, on_error=on_error))
    typed_rows: list[T] = list(
        iter_loads(lines, codec=codec, skip_errors=skip_errors, on_error=on_error)
    )
    return typed_rows


def dump[T](
    rows: Iterable[T],
    fp: io.TextIOBase | _Writer,
    *,
    codec: _Codec[T] | None = None,
) -> int:
    """Write *rows* as NDJSON to file object *fp*; returns the number of records written.

    The ``jsonx.dump(obj, fp)`` twin, plural and counting. Streams row by row (no
    full-corpus buffering) and picks ``bytes`` vs ``str`` output from whether *fp*
    is a text handle — so it serves ``open(p, "w")`` and ``open(p, "wb")`` /
    ``gzip.open`` alike. Short writes finish the current record before advancing.
    A zero count raises ``BlockingIOError``. Standard ``io.IOBase`` streams also
    use None for would-block; generic write-only sinks may use None to acknowledge
    a complete write. Any prefix already written remains in the caller-owned sink.
    """
    n = 0
    encode = _encoder(codec)
    require_count = isinstance(fp, io.IOBase)
    if isinstance(fp, io.TextIOBase):
        for row in rows:
            _write_all(fp.write, encode(row).decode(), require_count=require_count)
            n += 1
    else:
        for row in rows:
            _write_all(fp.write, encode(row), require_count=require_count)
            n += 1
    return n


@overload
def load[T](
    fp: Iterable[str | bytes],
    *,
    codec: _Codec[T],
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[T]: ...


@overload
def load(
    fp: Iterable[str | bytes],
    *,
    codec: None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[Json]: ...


def load[T](
    fp: Iterable[str | bytes],
    *,
    codec: _Codec[T] | None = None,
    skip_errors: bool = False,
    on_error: Callable[[NDJSONDecodeError], None] | None = None,
) -> list[Json] | list[T]:
    """Read every NDJSON record from file object *fp* into a ``list`` (the ``jsonx.load`` twin)."""
    if codec is None:
        return list(iter_loads(fp, skip_errors=skip_errors, on_error=on_error))
    typed_rows: list[T] = list(
        iter_loads(fp, codec=codec, skip_errors=skip_errors, on_error=on_error)
    )
    return typed_rows
