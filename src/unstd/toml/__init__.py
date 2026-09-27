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
from typing import TYPE_CHECKING


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
        dump,
        dumps,
        inline_table,
        item,
        nl,
        parse,
        table,
    )

    _HAVE_TOMLKIT = True
else:
    try:
        from tomlkit import (
            aot,
            array,
            comment,
            document,
            dump,
            dumps,
            inline_table,
            item,
            nl,
            parse,
            table,
        )

        _HAVE_TOMLKIT = True
    except ImportError:  # write path has no stdlib equivalent — tomllib is read-only
        from unstd._extra import missing

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
