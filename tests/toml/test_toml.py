"""Adversarial + parity tests for ``unstd.toml``.

Two contracts to pin:

1. **Read == stdlib.** ``loads`` / ``load`` *are* stdlib ``tomllib`` — so they must
   parse a representative document (tables, arrays, datetimes, nested, array-of-
   tables) byte-identically to ``tomllib`` itself, keep ``load``'s binary-file
   requirement, and re-export ``TOMLDecodeError``.
2. **Write fails loud without the extra.** The write half (``dumps``/``dump``/
   ``parse``/``document``) has no stdlib equivalent, so on a base install it must
   raise a clear ImportError naming the ``toml`` extra — proven here by forcing the
   exact ``_HAVE_TOMLKIT is False`` branch the production guard takes. When the
   extra *is* present, the write path round-trips and preserves comments/layout.
"""

from __future__ import annotations

import datetime as dt
import importlib
import io
import sys
import tomllib

import pytest

from unstd import toml


# A representative document exercising every TOML value class the read path must
# hand back exactly as stdlib tomllib does.
DOC = """\
title = "unstd"                 # a trailing comment
port = 8080
ratio = 0.75
enabled = true
tags = ["a", "b", "c"]
created = 1979-05-27T07:32:00Z
day = 1979-05-27
alarm = 07:32:00

[server]
host = "localhost"
ports = [8001, 8002]

[server.tls]
enabled = false

[[worker]]
name = "w1"

[[worker]]
name = "w2"
"""


# ── read path: loads/load ARE tomllib ──────────────────────────────────────────


def test_loads_is_byte_identical_to_stdlib_tomllib() -> None:
    """Test loads is byte identical to stdlib tomllib."""
    assert toml.loads(DOC) == tomllib.loads(DOC)


def test_loads_hands_back_the_full_value_taxonomy() -> None:
    """Test loads hands back the full value taxonomy."""
    data = toml.loads(DOC)
    assert data["title"] == "unstd"
    assert isinstance(data["port"], int)
    assert data["port"] == 8080
    assert data["ratio"] == 0.75
    assert data["enabled"] is True
    assert data["tags"] == ["a", "b", "c"]
    # datetime / date / time land as native aware / naive objects, exactly like tomllib.
    assert data["created"] == dt.datetime(1979, 5, 27, 7, 32, tzinfo=dt.UTC)
    assert data["day"] == dt.date(1979, 5, 27)
    assert data["alarm"] == dt.time(7, 32)
    # nested tables + array-of-tables preserve structure.
    assert data["server"]["host"] == "localhost"
    assert data["server"]["ports"] == [8001, 8002]
    assert data["server"]["tls"]["enabled"] is False
    assert [w["name"] for w in data["worker"]] == ["w1", "w2"]


def test_load_reads_a_binary_file_like_stdlib(tmp_path) -> None:
    """Test load reads a binary file like stdlib."""
    p = tmp_path / "cfg.toml"
    p.write_text(DOC, encoding="utf-8")
    with p.open("rb") as fp:
        assert toml.load(fp) == tomllib.loads(DOC)


def test_load_rejects_a_text_mode_file_faithful_to_tomllib(tmp_path) -> None:
    # tomllib.load requires binary mode; unstd.toml.load forwards that contract.
    """Test load rejects a text mode file faithful to tomllib."""
    p = tmp_path / "cfg.toml"
    p.write_text(DOC, encoding="utf-8")
    with p.open("r", encoding="utf-8") as fp, pytest.raises(TypeError):
        toml.load(fp)


def test_malformed_toml_raises_the_reexported_decode_error() -> None:
    """Test malformed toml raises the reexported decode error."""
    assert toml.TOMLDecodeError is tomllib.TOMLDecodeError
    with pytest.raises(toml.TOMLDecodeError):
        toml.loads("not = = valid")


# ── write path: fails loud without the extra (forced branch = a base install) ───


@pytest.fixture
def base_install(monkeypatch):
    """``unstd.toml`` as imported on a base install: ``tomlkit`` unimportable.

    Re-runs the module's real import-time branch rather than flipping a flag, so
    the placeholders under test are the ones a base install actually binds.
    """
    monkeypatch.setitem(sys.modules, "tomlkit", None)
    yield importlib.reload(toml)
    monkeypatch.undo()
    importlib.reload(toml)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("dumps", ({"a": 1},)),
        ("dump", ({"a": 1}, io.StringIO())),
        ("parse", ("a = 1",)),
        ("document", ()),
        ("table", ()),
        ("array", ()),
        ("inline_table", ()),
        ("aot", ()),
        ("comment", ("hi",)),
        ("nl", ()),
        ("item", (1,)),
    ],
)
def test_write_path_raises_clear_importerror_without_extra(
    base_install, name, args
) -> None:
    """Every write-path name imports cleanly on a base install and raises naming the extra when called."""
    assert base_install._HAVE_TOMLKIT is False
    with pytest.raises(ImportError, match=r"unstd\[toml\]"):
        getattr(base_install, name)(*args)


def test_read_path_survives_a_base_install(base_install) -> None:
    """The read half is stdlib, so a missing ``tomlkit`` must not touch it."""
    assert base_install.loads(DOC) == tomllib.loads(DOC)


# ── write path: round-trip + style preservation (only when the extra is present) ─


needs_tomlkit = pytest.mark.skipif(
    not toml._HAVE_TOMLKIT, reason="write path requires the 'toml' extra (tomlkit)"
)


@needs_tomlkit
def test_dumps_roundtrips_through_loads() -> None:
    """Test dumps roundtrips through loads."""
    parsed = toml.loads(DOC)
    assert toml.loads(toml.dumps(parsed)) == parsed


@needs_tomlkit
def test_dump_writes_a_reparseable_document_to_a_text_file() -> None:
    """Test dump writes a reparseable document to a text file."""
    parsed = toml.loads(DOC)
    buf = io.StringIO()
    toml.dump(parsed, buf)
    assert toml.loads(buf.getvalue()) == parsed


@needs_tomlkit
def test_parse_preserves_comments_and_layout_on_dumps() -> None:
    """Test parse preserves comments and layout on dumps."""
    doc = toml.parse(DOC)
    doc["port"] = 9090  # mutate one value…
    out = toml.dumps(doc)
    assert "# a trailing comment" in out  # …comment survives the round-trip
    assert "port = 9090" in out  # …and the edit landed
    assert toml.loads(out)["title"] == "unstd"  # untouched value intact


@needs_tomlkit
def test_document_builds_valid_toml_from_scratch() -> None:
    """Test document builds valid toml from scratch."""
    doc = toml.document()
    doc["name"] = "unstd"
    doc["count"] = 3
    assert toml.loads(toml.dumps(doc)) == {"name": "unstd", "count": 3}


# ── from-scratch node constructors (table/array/inline_table/aot/comment/…) ──────


@needs_tomlkit
def test_table_and_aot_build_an_array_of_tables_from_scratch() -> None:
    """Test table and aot build an array of tables from scratch."""
    doc = toml.document()
    servers = toml.aot()
    for name in ("alpha", "beta"):
        t = toml.table()
        t["name"] = name
        servers.append(t)
    doc["server"] = servers
    assert toml.loads(toml.dumps(doc))["server"] == [
        {"name": "alpha"},
        {"name": "beta"},
    ]


@needs_tomlkit
def test_inline_table_and_array_round_trip() -> None:
    """Test inline table and array round trip."""
    doc = toml.document()
    point = toml.inline_table()
    point["x"] = 1
    point["y"] = 2
    doc["point"] = point
    doc["tags"] = toml.array('["a", "b"]')
    parsed = toml.loads(toml.dumps(doc))
    assert parsed == {"point": {"x": 1, "y": 2}, "tags": ["a", "b"]}
    assert "{x = 1, y = 2}" in toml.dumps(doc)  # stays inline, not a [point] table


@needs_tomlkit
def test_comment_and_nl_attach_standalone_layout_nodes() -> None:
    """Test comment and nl attach standalone layout nodes."""
    doc = toml.document()
    doc.add(toml.comment("generated by a test"))
    doc.add(toml.nl())
    doc["ok"] = True
    out = toml.dumps(doc)
    assert "# generated by a test" in out
    assert toml.loads(out) == {"ok": True}


@needs_tomlkit
def test_item_wraps_a_plain_value_as_a_tomlkit_node() -> None:
    """Test item wraps a plain value as a tomlkit node."""
    doc = toml.document()
    doc["values"] = toml.item([1, 2, 3])
    assert toml.loads(toml.dumps(doc)) == {"values": [1, 2, 3]}


# ── read/write spec-version skew (TOML 1.1 write vs 1.0-only stdlib read) ─────────


@needs_tomlkit
def test_tomlkit_writes_toml_that_stdlib_tomllib_can_still_read_for_common_shapes() -> (
    None
):
    # Everything this module's OWN write helpers can produce from ordinary Python
    # values stays within the TOML-1.0.0 subset stdlib understands — the 1.1-only
    # surface (documented above) is only reachable via hand-authored/1.1-only
    # input, not anything `dumps`/`document`/`table`/... emits on its own.
    """Test tomlkit writes toml that stdlib tomllib can still read for common shapes."""
    doc = toml.document()
    doc["s"] = "text"
    doc["n"] = 42
    doc["f"] = 1.5
    doc["b"] = True
    doc["arr"] = [1, 2, 3]
    doc["nested"] = {"k": "v"}
    assert toml.loads(toml.dumps(doc)) == tomllib.loads(toml.dumps(doc))
