"""Style-preserving edits compared with exact, reviewed before/after fixtures.

Each expected document is the source with only the edited line(s) changed by
hand: comments, blank lines, key order, number/string spelling and multi-line
array layout must survive byte for byte. ``tomllib`` independently confirms
that every edited document still means what the edit says.
"""

from __future__ import annotations

import tomllib

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


@pytest.fixture(autouse=True)
def tomlkit_backend() -> None:
    require_backend("tomlkit")


SOURCE = """\
# Service configuration
title = "unstd"   # display name
mask = 0o755
limit = 1_000
pattern = 'C:\\no\\escapes'

[server]
host = "localhost"
port = 8080 # listen port
started = 1979-05-27 07:32:00Z
ports = [
  8001,  # primary
  8002,
]

[database]
url = "postgres://db"
"""

AWKWARD = [
    SOURCE,
    "a = 1\r\nb = 'two'\r\n",
    "\tindented = true\n\n\n# trailing comment without newline",
    "text = \"\"\"\nfirst\\\n  second\n\"\"\"\nraw = '''\nkeep \\n literal'''\n",
    "point = { x = 1, y = 2 }\nsite.owner.name = 'x'\n[[bin]]\nname = 'a'\n\n[[bin]]\nname = 'b'\n",
    "f = +1.0e+10\ng = -0.0\nh = inf\nhex = 0xDEAD_beef\nbin = 0b1010\n",
]


@pytest.mark.parametrize("source", AWKWARD)
def test_unedited_parse_reemits_the_exact_source(source: str) -> None:
    assert toml.dumps(toml.parse(source)) == source


def test_value_replacement_keeps_trailing_comment_and_neighbours() -> None:
    doc = toml.parse(SOURCE)
    doc["server"]["port"] = 9090
    out = toml.dumps(doc)
    assert out == SOURCE.replace(
        "port = 8080 # listen port", "port = 9090 # listen port"
    )
    assert tomllib.loads(out)["server"]["port"] == 9090


def test_key_deletion_removes_only_that_line() -> None:
    doc = toml.parse(SOURCE)
    del doc["title"]
    out = toml.dumps(doc)
    assert out == SOURCE.replace('title = "unstd"   # display name\n', "")
    assert "title" not in tomllib.loads(out)


def test_new_key_is_appended_to_its_own_table() -> None:
    doc = toml.parse(SOURCE)
    doc["database"]["pool"] = 5
    out = toml.dumps(doc)
    assert out == SOURCE + "pool = 5\n"
    assert tomllib.loads(out)["database"] == {"url": "postgres://db", "pool": 5}


def test_new_table_is_appended_after_existing_tables() -> None:
    doc = toml.parse(SOURCE)
    cache = toml.table()
    cache["ttl"] = 30
    doc["cache"] = cache
    out = toml.dumps(doc)
    assert out == SOURCE + "\n[cache]\nttl = 30\n"
    assert tomllib.loads(out)["cache"] == {"ttl": 30}


def test_string_edit_changes_only_the_value_spelling() -> None:
    doc = toml.parse(SOURCE)
    doc["database"]["url"] = 'postgres://"quoted"\\db'
    out = toml.dumps(doc)
    assert out == SOURCE.replace(
        'url = "postgres://db"', 'url = "postgres://\\"quoted\\"\\\\db"'
    )
    assert tomllib.loads(out)["database"]["url"] == 'postgres://"quoted"\\db'


def test_multiline_array_element_edit_keeps_layout_and_comment() -> None:
    doc = toml.parse(SOURCE)
    doc["server"]["ports"][1] = 9002
    out = toml.dumps(doc)
    assert out == SOURCE.replace("  8002,\n", "  9002,\n")
    assert tomllib.loads(out)["server"]["ports"] == [8001, 9002]


def test_edits_compose_and_untouched_spellings_survive() -> None:
    doc = toml.parse(SOURCE)
    doc["limit"] = 2000
    doc["server"]["host"] = "0.0.0.0"  # noqa: S104 — TOML value, no socket is opened.
    out = toml.dumps(doc)
    assert out == SOURCE.replace("limit = 1_000", "limit = 2000").replace(
        'host = "localhost"', 'host = "0.0.0.0"'
    )
    # 0o755, the literal string and the space-delimited datetime keep their spelling.
    assert "mask = 0o755\n" in out
    assert "pattern = 'C:\\no\\escapes'\n" in out
    assert "started = 1979-05-27 07:32:00Z\n" in out
    expected = tomllib.loads(SOURCE)
    expected["limit"] = 2000
    expected["server"]["host"] = "0.0.0.0"  # noqa: S104 — expected TOML value only.
    assert tomllib.loads(out) == expected


def test_failed_edit_leaves_document_unchanged() -> None:
    doc = toml.parse(SOURCE)
    with pytest.raises(TypeError):
        doc["server"]["port"] = object()
    assert toml.dumps(doc) == SOURCE
