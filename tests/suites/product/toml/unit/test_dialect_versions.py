"""TOML 1.0.0 versus 1.1.0 fixtures for the documented read/write skew.

Each fixture uses syntax new in TOML 1.1.0 with its value hand-derived from the
1.1.0 specification. The stdlib reader accepts it only from Python 3.15; the
editable parser always accepts it. The 1.0 spelling of the same value is the
common dialect and must read identically through both halves.
"""

from __future__ import annotations

import datetime as dt
import sys
import tomllib

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


STDLIB_READS_1_1 = sys.version_info >= (3, 15)

# (case, TOML 1.1-only document, equivalent TOML 1.0 document, value)
FIXTURES = [
    ("time-without-seconds", "t = 07:32\n", "t = 07:32:00\n", {"t": dt.time(7, 32)}),
    (
        "datetime-without-seconds",
        "t = 1979-05-27T07:32Z\n",
        "t = 1979-05-27T07:32:00Z\n",
        {"t": dt.datetime(1979, 5, 27, 7, 32, tzinfo=dt.UTC)},
    ),
    ("escape-e", 's = "\\e"\n', 's = "\\u001b"\n', {"s": "\x1b"}),
    ("escape-x", 's = "\\x41\\xe9"\n', 's = "A\\u00e9"\n', {"s": "Aé"}),
    (
        "inline-table-newlines",
        "p = {\n  x = 1,\n  y = 2,\n}\n",
        "p = {x = 1, y = 2}\n",
        {"p": {"x": 1, "y": 2}},
    ),
]
IDS = [f[0] for f in FIXTURES]


@pytest.mark.parametrize(("v11", "v10", "value"), [f[1:] for f in FIXTURES], ids=IDS)
def test_common_dialect_spelling_reads_identically(
    v11: str, v10: str, value: dict[str, object]
) -> None:
    assert toml.loads(v10) == value
    require_backend("tomlkit")
    assert toml.parse(v10).unwrap() == value


@pytest.mark.parametrize(("v11", "v10", "value"), [f[1:] for f in FIXTURES], ids=IDS)
def test_stdlib_reader_follows_its_interpreter_dialect(
    v11: str, v10: str, value: dict[str, object]
) -> None:
    if STDLIB_READS_1_1:
        assert toml.loads(v11) == value
    else:
        with pytest.raises(toml.TOMLDecodeError):
            toml.loads(v11)


@pytest.mark.parametrize(("v11", "v10", "value"), [f[1:] for f in FIXTURES], ids=IDS)
def test_editable_parser_reads_and_preserves_1_1_documents(
    v11: str, v10: str, value: dict[str, object]
) -> None:
    require_backend("tomlkit")
    doc = toml.parse(v11)
    assert doc.unwrap() == value
    assert toml.dumps(doc) == v11


def test_written_control_characters_read_back_through_parse() -> None:
    require_backend("tomlkit")
    text = "".join(map(chr, range(0x20))) + "\x7f"
    assert toml.parse(toml.dumps({"s": text})).unwrap() == {"s": text}


def test_writer_spells_ordinary_strings_in_the_common_dialect() -> None:
    require_backend("tomlkit")
    assert toml.dumps({"s": "\x1b"}) == 's = "\\u001b"\n'
    assert tomllib.loads(toml.dumps({"s": "\x1b"})) == {"s": "\x1b"}
