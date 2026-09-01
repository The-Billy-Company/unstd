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
(reads keep working) and only the write calls fail loud — the same posture as
[`serde.structs`](../serde/README.md#structs--typed-msgspec-codecs),
`ids.hash`, and `time.dateutil`.

## Style preservation

`tomlkit` treats a TOML document as an editable, comment-aware tree: `parse` →
mutate → `dumps` rewrites a config file **in place** without reflowing the parts
you didn't touch (comments, key order, whitespace, array layout all survive).
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
backported. Nothing in this module emits 1.1-only syntax on its own, but a
document built with 1.1-only constructs (a caller's data, or hand-authored
input) can fail `loads`/`load` even though `dumps` wrote it without complaint.
Read such a document back with `tomlkit.parse` (or this module's `parse`) — not
`loads` — until the target interpreter is 3.15+.

## Prior art

- **stdlib `tomllib`** — [PEP 680](https://peps.python.org/pep-0680/); based on
  [`tomli`](https://github.com/hukkin/tomli) by Taneli Hukkinen. A strict,
  spec-compliant TOML 1.0.0 parser, read-only on purpose.
- **[`tomlkit`](https://github.com/sdispater/tomlkit)** — Sébastien Eustace; the
  style-preserving TOML engine behind Poetry.
