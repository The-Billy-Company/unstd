"""TOML — stdlib ``tomllib`` read path + a style-preserving ``tomlkit`` write path.

A faithful stand-in for the stdlib ``tomllib`` module that *adds the write half*
``tomllib`` deliberately omits. Swap the import and the surface is unchanged for
reads::

    import tomllib  # →
    from unstd import toml  # toml.loads / toml.load, same surface, plus a writer

Split backend, because the two halves have different stdlib support:

- **READ** — :func:`loads` / :func:`load` forward straight to stdlib ``tomllib``
  (PEP 680, Python 3.11+, *always* present), so a base ``unstd`` install parses
  TOML with zero third-party dependency and byte-for-byte the semantics stdlib
  gives — including ``load``'s binary-file requirement, which is preserved.
- **WRITE** — :func:`dumps` / :func:`dump` (and the style-preserving
  :func:`parse` / :func:`document` builders) have **no stdlib equivalent**:
  ``tomllib`` is read-only by design. They ride the ``toml`` extra
  (``tomlkit``); imported without it the module still loads (reads keep working),
  and only the write calls raise a clear, actionable ImportError naming the extra
  — the same fail-loud-on-a-capability-with-no-fallback posture as
  ``serde.structs`` / ``crypto`` / ``time.zoned``.

The write path is *round-trip / style-preserving*: ``tomlkit`` retains comments,
key order, whitespace, and array layout, so ``parse`` → mutate → :func:`dumps`
rewrites a config file in place without reflowing the parts you didn't touch.
Building a document from nothing (no existing TOML to round-trip) uses the same
``tomlkit`` node constructors, re-exported here so a caller never has to reach
past this module for them: :func:`table` / :func:`array` / :func:`inline_table`
/ :func:`aot` (array of tables) / :func:`comment` / :func:`nl` / :func:`item`.

**Read/write spec-version skew (Python < 3.15).** ``tomlkit`` 0.15+ writes
TOML 1.1.0 (ratified 2025-12-18); stdlib ``tomllib`` stays TOML-1.0.0-only
through Python 3.14 — 1.1 support lands in 3.15
(https://docs.python.org/3.15/whatsnew/3.15.html), not backported. So on every
interpreter this package currently targets, a document containing 1.1-only
syntax (seconds-optional local times, a handful of new escapes) that ``dumps``
happily writes can fail to round-trip back through :func:`loads`. Nothing in
this module *emits* 1.1-only syntax on its own — it only exists if a caller's
data does — but a from-scratch document built with 1.1-only constructs should
be read back with ``tomlkit.parse``, not :func:`loads`, until you are on 3.15+.

Prior art:

- **stdlib ``tomllib``** (PEP 680; based on ``tomli`` by Taneli Hukkinen) — the
  read half; a strict, spec-compliant TOML 1.0.0 parser, read-only on purpose.
- **``tomlkit``** (Sébastien Eustace; the TOML engine behind Poetry) — the write
  half; a style-preserving round-trip parser/serializer that treats a TOML
  document as an editable, comment-aware tree.
"""

from __future__ import annotations

import tomllib
from typing import IO, TYPE_CHECKING, cast


if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    import tomlkit
    from tomlkit import TOMLDocument
    from tomlkit.items import AoT, Array, Comment, InlineTable, Item, Table, Whitespace

    _HAVE_TOMLKIT = True
else:
    try:
        import tomlkit

        _HAVE_TOMLKIT = True
    except ImportError:  # write path has no stdlib equivalent — tomllib is read-only
        tomlkit = None
        _HAVE_TOMLKIT = False


# Re-exported so ``except toml.TOMLDecodeError`` mirrors ``except tomllib.TOMLDecodeError``.
TOMLDecodeError = tomllib.TOMLDecodeError

__all__ = [
    "TOMLDecodeError",
    "aot",
    "array",
    "comment",
    "document",
    "dump",
    "dumps",
    "inline_table",
    "item",
    "load",
    "loads",
    "nl",
    "parse",
    "table",
]

# One shared, actionable hint for every write-path call when the extra is absent.
_WRITE_HINT = (
    "unstd.toml's write path (dumps/dump/parse/document) requires the 'toml' extra — "
    "install with: pip install 'unstd[toml]'. The stdlib tomllib is read-only (PEP 680); "
    "tomlkit provides the style-preserving TOML serializer it omits."
)


def _require_kit() -> None:
    """Raise an ImportError naming the ``toml`` extra when ``tomlkit`` is absent."""
    if not _HAVE_TOMLKIT:
        raise ImportError(_WRITE_HINT)


def loads(s: str) -> dict[str, object]:
    """Parse a TOML *string* into a ``dict`` — stdlib ``tomllib.loads`` (always available)."""
    return tomllib.loads(s)


def load(fp: IO[bytes]) -> dict[str, object]:
    """Parse TOML from a **binary** file object — stdlib ``tomllib.load``. Faithful to stdlib: ``tomllib`` requires the file be opened in binary mode (``open(p, "rb")``)."""
    return tomllib.load(fp)


def dumps(obj: Mapping[str, object]) -> str:
    """Serialize *obj* (a mapping, or a style-preserving :func:`parse` / :func:`document` result) back to a TOML *string* via ``tomlkit`` — comment-, order-, and layout-preserving. Raises :class:`ImportError` when the ``toml`` extra is absent (the stdlib has no TOML writer — ``tomllib`` is read-only)."""
    _require_kit()
    return tomlkit.dumps(obj)


def dump(obj: Mapping[str, object], fp: IO[str]) -> None:
    """Write *obj* as TOML to text file object *fp* — the ``dumps(...)``-then-write idiom.

    Requires the ``toml`` extra (``tomllib`` ships no writer).
    """
    fp.write(dumps(obj))


def parse(s: str) -> TOMLDocument:
    """Parse *s* into a style-preserving ``tomlkit`` document — mutate it and :func:`dumps` keeps the original comments and layout, unlike :func:`loads` which returns a plain ``dict``. Requires the ``toml`` extra."""
    _require_kit()
    return tomlkit.parse(s)


def document() -> TOMLDocument:
    """Return a new empty style-preserving ``tomlkit`` document to build up and :func:`dumps`.

    Requires the ``toml`` extra.
    """
    _require_kit()
    return tomlkit.document()


# ── from-scratch node constructors — building a document with no prior TOML to
# round-trip. Thin re-exports so a caller assembling one never has to import
# ``tomlkit`` directly; the guarded-optional posture is identical to the four
# functions above. See ``tomlkit.api`` for each constructor's own docstring.


def table(is_super_table: bool | None = None) -> Table:
    """Return a new, empty ``[table]`` node to populate and attach to a document."""
    _require_kit()
    return tomlkit.table(is_super_table=is_super_table)


def array(raw: str = "[]") -> Array:
    """Return a new ``[...]`` array node, optionally seeded from raw TOML array syntax."""
    _require_kit()
    return tomlkit.array(raw)


def inline_table() -> InlineTable:
    """Return a new, empty ``{ ... }`` inline-table node."""
    _require_kit()
    return tomlkit.inline_table()


def aot() -> AoT:
    """Return a new, empty array-of-tables (``[[name]]``) node."""
    _require_kit()
    return tomlkit.aot()


def comment(string: str) -> Comment:
    """Return a standalone ``# string`` comment node to insert between entries."""
    _require_kit()
    return tomlkit.comment(string)


def nl() -> Whitespace:
    """Return a blank-line node — the layout unit :func:`comment` sits beside."""
    _require_kit()
    return tomlkit.nl()


def item(value: Any) -> Item:
    """Wrap a plain Python value (``str``/``int``/``list``/``dict``/…) as a ``tomlkit`` node."""
    # tomlkit ships no stubs, so its constructor is `Any` at the boundary; `Item`
    # is the node type it is documented to return and the one this surface names.
    _require_kit()
    return cast("Item", tomlkit.item(value))
