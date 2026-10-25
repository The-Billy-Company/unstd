r"""TOML — stdlib ``tomllib`` read path + a style-preserving ``tomlkit`` write path.

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
happily writes can fail to round-trip back through :func:`loads`. :func:`dumps`
and :func:`dump` therefore keep plain Python data in the common dialect: ESC is
spelled ``\u001b`` (tomlkit alone writes the 1.1-only ``\e``), and values TOML
cannot represent in any version — a UTC offset that is not whole minutes, or a
time of day carrying an offset — raise ``ValueError`` instead of being written
as invalid TOML. Strings that are already tomlkit items (parsed text, or values
assigned into a parsed document) keep their spelling, so parse → edit → dump
stays byte-preserving; a parsed 1.1 document should be read back with
:func:`parse`, not :func:`loads`, until you are on 3.15+.

Prior art:

- **stdlib ``tomllib``** (PEP 680; based on ``tomli`` by Taneli Hukkinen) — the
  read half; a strict, spec-compliant TOML 1.0.0 parser, read-only on purpose.
- **``tomlkit``** (Sébastien Eustace; the TOML engine behind Poetry) — the write
  half; a style-preserving round-trip parser/serializer that treats a TOML
  document as an editable, comment-aware tree.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 # public runtime annotations
import tomllib
from typing import IO, TYPE_CHECKING, Any


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

# The read half *is* stdlib: same functions, same errors, no forwarding frame.
loads = tomllib.loads
load = tomllib.load
TOMLDecodeError = tomllib.TOMLDecodeError

# The write half *is* tomlkit (typed, same signatures) when the extra is present,
# and a named placeholder when it is not — so importing this module never fails,
# and reads keep working on a base install.
if TYPE_CHECKING:
    from tomlkit import (
        aot,
        array,
        comment,
        document,
        inline_table,
        item,
        nl,
        parse,
        table,
    )
    from tomlkit import (
        dumps as _dumps,
    )

    from unstd.toml.dialect import _common_mapping

    _HAVE_TOMLKIT = True
else:
    try:
        from tomlkit import (
            aot,
            array,
            comment,
            document,
            inline_table,
            item,
            nl,
            parse,
            table,
        )
        from tomlkit import (
            dumps as _dumps,
        )

        from unstd.toml.dialect import _common_mapping

        _HAVE_TOMLKIT = True
    except ImportError:  # write path has no stdlib equivalent — tomllib is read-only
        from unstd.extras import missing

        _HAVE_TOMLKIT = False
        (
            aot, array, comment, document, dump, dumps,
            inline_table, item, nl, parse, table,
        ) = (
            missing(f"toml.{name}", "toml")
            for name in (
                "aot", "array", "comment", "document", "dump", "dumps",
                "inline_table", "item", "nl", "parse", "table",
            )
        )  # fmt: skip

if _HAVE_TOMLKIT:

    def dumps(data: Mapping[str, Any], sort_keys: bool = False) -> str:
        r"""``tomlkit.dumps`` kept to the TOML 1.0 dialect ``tomllib`` reads.

        Raises ``ValueError`` for an offset that is not whole minutes or a time of
        day with an offset; plain strings spell ESC ``\u001b``, not ``\e``.
        """
        return _dumps(_common_mapping(data), sort_keys=sort_keys)

    def dump(data: Mapping[str, Any], fp: IO[str], *, sort_keys: bool = False) -> None:
        """Write :func:`dumps` output to *fp*; nothing is written if it raises."""
        fp.write(dumps(data, sort_keys=sort_keys))
