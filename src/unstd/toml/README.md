---
doc_radar:
  sentinels:
    - file: __init__.py
      contains: ['from unstd.toml.dialect import']
    - file: dialect.py
      contains: ['def _common_mapping(']
---

# `unstd.toml`

Part of [`unstd`](../README.md). A faithful stand-in for the stdlib
`tomllib` module that **adds the write half** `tomllib` deliberately omits —
reads ride stdlib, writes ride a style-preserving backend.

```python
from unstd import toml

cfg = toml.loads(text)  # str  -> dict   (stdlib tomllib)
cfg = toml.load(open("f.toml", "rb"))  # binary file -> dict (stdlib tomllib)
text = toml.dumps(cfg)  # mapping -> str  (tomlkit; needs the extra)
toml.dump(cfg, open("f.toml", "w"))  # write mapping to a text file

doc = toml.parse(text)  # style-preserving tomlkit document
doc["key"] = "value"  # mutate…
text = toml.dumps(doc)  # …and re-emit, keeping comments + layout

# building a document with nothing to round-trip:
doc = toml.document()
doc.add(toml.comment("generated"))
doc["servers"] = toml.aot()
doc["servers"].append(toml.table())
text = toml.dumps(doc)
```

## Backend — split by capability

| Half      | Surface                                                                                                         | Backend                    | Without the `toml` extra                           |
| --------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------- | -------------------------------------------------- |
| **read**  | `loads` · `load`                                                                                                   | stdlib `tomllib` (PEP 680) | works — `tomllib` is always present (Python 3.11+) |
| **write** | `dumps` · `dump` · `parse` · `document` · `table` · `array` · `inline_table` · `aot` · `comment` · `nl` · `item`   | `tomlkit`                  | raises a clear `ImportError` naming the extra      |

The read path is **pure stdlib**, so a base `unstd` install parses TOML with zero
third-party dependency and byte-for-byte `tomllib` semantics — including `load`'s
requirement that the file be opened in **binary** mode. The write path has **no
stdlib equivalent** (`tomllib` is read-only _by design_), so it hard-requires the
`toml` extra (`pip install 'unstd[toml]'`); the module still imports without it
(reads keep working) and calls needing tomlkit raise the installation error.

## Style preservation

`tomlkit` treats a TOML document as an editable, comment-aware tree: `parse` →
mutate → `dumps` returns updated text while retaining comments, key order,
whitespace, and array layout in the document. Write that text back to the file
to persist an edit.
Use `loads` when you only need the values as a plain `dict`; use `parse` /
`document` when you are editing a file a human will read again.

## Building from scratch

No prior TOML to round-trip? `document()` plus the node constructors
(`table` / `array` / `inline_table` / `aot` / `comment` / `nl` / `item`) build
a document the same way `tomlkit` does natively — re-exported here so a caller
never has to `import tomlkit` directly just to construct one.

## Read/write spec-version skew (until Python 3.15)

`tomlkit` 0.15+ writes **TOML 1.1.0** (ratified 2025-12-18); stdlib `tomllib`
stays **TOML-1.0.0-only** through Python 3.14 — 1.1 support
[lands in 3.15](https://docs.python.org/3.15/whatsnew/3.15.html), not
backported. `dumps`/`dump` therefore keep plain Python data in TOML 1.0:
ESC is spelled `\u001b` (tomlkit alone writes the 1.1-only `\e`), and UTC
offsets that are not whole minutes, or a `time` carrying an offset, raise
`ValueError` before anything is written — no TOML version can represent them.
Existing tomlkit items keep their spelling, so a document parsed from (or
built with) 1.1-only constructs can still fail `loads`/`load` after `dumps` on
Python 3.12–3.14. Read those documents with `tomlkit.parse` (or this module's
`parse`) on older interpreters. Python 3.15 reads TOML 1.1 through `loads`/`load`.

## Prior art

- **stdlib `tomllib`** — [PEP 680](https://peps.python.org/pep-0680/); based on
  [`tomli`](https://github.com/hukkin/tomli) by Taneli Hukkinen. A strict,
  spec-compliant parser, read-only on purpose; TOML 1.1.0 from Python 3.15,
  and TOML 1.0.0 on earlier supported interpreters.
- **[`tomlkit`](https://github.com/sdispater/tomlkit)** — Sébastien Eustace; the
  style-preserving TOML engine behind Poetry.

## Source ownership

This package is one TOML code leaf. [`__init__.py`](__init__.py) keeps stdlib
read aliases, optional writing imports, style-preserving builders and public
write calls. [`dialect.py`](dialect.py) validates values and normalizes plain
data to the common TOML dialect; it loads only when the writing backend is
available. Keeping these two cohesive modules together avoids a separate
package for one normalization module.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public read/write API and document builders |
| [`dialect.py`](dialect.py) | Common-dialect value validation and normalization |

Run the contracts from the repository root with
`uv run --no-sync python -m pytest tests/suites/product/toml -q`.
