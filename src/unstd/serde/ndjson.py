r"""NDJSON — newline-delimited JSON, one JSON value per line.

NDJSON (aka JSON-lines / ``.jsonl``) is the append-friendly, stream-friendly
sibling of a JSON array: ``\\n``-separated JSON values, each independently
parseable. It is what trace exports, eval datasets, log shipping, and machine
learning corpora are written as — large files you want to append to and read a
line at a time without materializing the whole array.

This module is a thin layer over :mod:`jsonx` so the whole ``serde`` package
rides one JSON backend — never re-import orjson here. Each record is encoded with
``jsonx.dumps``/``jsonx.dumpb`` (compact UTF-8) and decoded with ``jsonx.loads``,
so NDJSON inherits jsonx's backend + stdlib fallback transparently.

Conventions:

- **Trailing newline per record.** ``dumps``/``dumpb``/``write`` terminate
  *every* line — including the last — so the output is append-safe (a following
  writer starts on a fresh line) and matches the ``json.dumps(x) + "\\n"``
  idiom the migrated call-sites produced.
- **Blank lines are skipped** on read (interior *and* trailing), so a file with
  a trailing newline round-trips cleanly.
- **Malformed lines are strict by default** — ``iter_loads``/``loads``/``read``
  raise :class:`NDJSONDecodeError` tagged with the 1-based line number. Pass
  ``skip_errors=True`` for the lenient "drop the bad line, keep the rest" mode
  some tolerant readers want.
"""

from __future__ import annotations

import io
from typing import IO, TYPE_CHECKING

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from unstd.serde.jsonx import Json


__all__ = [
    "NDJSONDecodeError",
    "dumpb",
    "dumps",
    "iter_loads",
    "loads",
    "read",
    "write",
]


class NDJSONDecodeError(ValueError):
    """A line failed to parse — carries its 1-based ``lineno`` for context."""

    def __init__(self, lineno: int, err: Exception) -> None:
        """Initialize the instance."""
        self.lineno = lineno
        self.err = err
        super().__init__(f"line {lineno}: {err}")


def dumps(rows: Iterable[object]) -> str:
    """Serialize *rows* to an NDJSON ``str`` — one ``jsonx.dumps`` per newline-terminated line."""
    return "".join(f"{jsonx.dumps(r)}\n" for r in rows)


def dumpb(rows: Iterable[object]) -> bytes:
    """Serialize *rows* straight to NDJSON ``bytes`` (``jsonx.dumpb``) for file/socket writes."""
    return b"".join(jsonx.dumpb(r) + b"\n" for r in rows)


def iter_loads(
    lines: Iterable[str | bytes], *, skip_errors: bool = False
) -> Iterator[Json]:
    """Stream-parse *lines* (a file object, ``splitlines()``, any line iterable) lazily.

    Consumes and yields one line at a time — never materializes the whole input,
    the property that matters for large trace/eval files. Blank / whitespace-only
    lines are skipped. A malformed line raises :class:`NDJSONDecodeError` with its
    1-based line number, unless *skip_errors* drops it silently.
    """
    for lineno, line in enumerate(lines, 1):
        if not (stripped := line.strip()):
            continue
        try:
            yield jsonx.loads(stripped)
        except jsonx.JSONDecodeError as exc:
            if skip_errors:
                continue
            raise NDJSONDecodeError(lineno, exc) from exc


def loads(text: str | bytes, *, skip_errors: bool = False) -> list[Json]:
    r"""Parse a whole NDJSON blob into a ``list`` — blank lines skipped, trailing newline tolerated.

    Splits strictly on ``\\n`` (not ``str.splitlines``, which also breaks on
    ``\\r``, U+0085 NEL, U+2028/U+2029, VT/FF): those code points occur *inside*
    JSON string bodies (scraped web text, LLM output) where orjson leaves them
    unescaped, and over-splitting there silently corrupts a record. A ``\\r\\n``
    writer is still tolerated — :func:`iter_loads` strips the trailing ``\\r``.
    """
    lines = (
        text.split(b"\n") if isinstance(text, bytes | bytearray) else text.split("\n")
    )
    return list(iter_loads(lines, skip_errors=skip_errors))


def write(fp: io.TextIOBase | IO[bytes], rows: Iterable[object]) -> int:
    """Write *rows* as NDJSON to file object *fp*; returns the number of records written.

    Streams row-by-row (no full-corpus buffering) and picks ``bytes`` vs ``str``
    output from whether *fp* is a text handle — so it serves both
    ``open(p, "w")`` and ``open(p, "wb")`` / ``gzip.open`` (the ``jsonx.dump``
    twin, plural + counting).
    """
    n = 0
    if isinstance(fp, io.TextIOBase):
        for row in rows:
            fp.write(f"{jsonx.dumps(row)}\n")
            n += 1
    else:
        for row in rows:
            fp.write(jsonx.dumpb(row) + b"\n")
            n += 1
    return n


def read(fp: IO[str] | IO[bytes], *, skip_errors: bool = False) -> list[Json]:
    """Read every NDJSON record from file object *fp* into a ``list`` (the ``jsonx.load`` twin)."""
    return list(iter_loads(fp, skip_errors=skip_errors))
