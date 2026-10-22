r"""Writer output must stay in TOML 1.0, the dialect stdlib ``tomllib`` reads.

tomlkit 0.15 spelled ESC as the 1.1-only ``\e`` and wrote seconds-precision
offsets (``+05:30:15``) and offset-carrying local times (``07:32:00+00:00``),
which no TOML version accepts. TOML 1.0 ``time-numoffset`` is ``HH:MM`` and a
``partial-time`` has no offset; ``\uXXXX`` is the 1.0 spelling of a control
character.
"""

from __future__ import annotations

import datetime as dt
import io
import tomllib

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


@pytest.fixture(autouse=True)
def tomlkit_backend() -> None:
    require_backend("tomlkit")


def _tz(**delta: float) -> dt.timezone:
    return dt.timezone(dt.timedelta(**delta))


@pytest.mark.parametrize(
    ("data", "text"),
    [
        ({"s": "\x1b"}, 's = "\\u001b"\n'),
        ({"s": 'a\x1b"\\e'}, 's = "a\\u001b\\"\\\\e"\n'),
        ({"k\x1b": 1}, '"k\\u001b" = 1\n'),
        ({"a": ["\x1b", {"b": "\x1b"}]}, 'a = ["\\u001b", {b = "\\u001b"}]\n'),
        ({"t": {"s": "\x1b"}}, '[t]\ns = "\\u001b"\n'),
        ({"s": ("x", "\x1b")}, 's = ["x", "\\u001b"]\n'),
    ],
)
def test_plain_strings_spell_esc_in_the_common_dialect(
    data: dict[str, object], text: str
) -> None:
    assert toml.dumps(data) == text
    assert tomllib.loads(text) == {
        k: list(v) if isinstance(v, tuple) else v for k, v in data.items()
    }


def test_parsed_items_keep_their_spelling() -> None:
    source = "s = \"\\e\"\nlit = 'C:\\e'\n"
    assert toml.dumps(toml.parse(source)) == source


@pytest.mark.parametrize(
    "value",
    [
        dt.datetime(2020, 1, 1, tzinfo=_tz(hours=5, minutes=30, seconds=15)),
        dt.datetime(2020, 1, 1, tzinfo=_tz(hours=-1, microseconds=-1)),
        dt.time(7, 32, tzinfo=dt.UTC),
        dt.time(7, 32, tzinfo=_tz(hours=2)),
    ],
    ids=["seconds-offset", "microsecond-offset", "utc-time", "offset-time"],
)
@pytest.mark.parametrize(
    "wrap",
    [
        lambda v: {"v": v},
        lambda v: {"t": {"n": [1, v]}},
        lambda v: {"aot": [{"v": v}]},
    ],
    ids=["root", "nested-array", "array-of-tables"],
)
def test_unrepresentable_temporal_values_are_refused_before_writing(
    value: object, wrap: object
) -> None:
    sink = io.StringIO()
    with pytest.raises(ValueError, match="TOML"):
        toml.dump(wrap(value), sink)  # type: ignore[operator]
    assert sink.getvalue() == ""


def test_values_assigned_into_a_document_are_checked() -> None:
    doc = toml.parse("# header\n[t]\nx = 1\n")
    doc["t"]["when"] = dt.datetime(2020, 1, 1, tzinfo=_tz(seconds=30))
    with pytest.raises(ValueError, match="whole minutes"):
        toml.dumps(doc)


@pytest.mark.parametrize(
    ("value", "spelled"),
    [
        (dt.datetime(2020, 1, 1, tzinfo=_tz(hours=-5, minutes=-30)), "-05:30"),
        (dt.datetime(2020, 1, 1, tzinfo=_tz(hours=23, minutes=59)), "+23:59"),
        (dt.datetime(2020, 1, 1, tzinfo=dt.UTC), "Z"),
    ],
)
def test_whole_minute_offsets_remain_writable(value: dt.datetime, spelled: str) -> None:
    text = toml.dumps({"v": value})
    assert text == f"v = 2020-01-01T00:00:00{spelled}\n"
    back = tomllib.loads(text)["v"]
    assert back == value
    assert back.utcoffset() == value.utcoffset()


def test_naive_time_and_offset_free_values_are_unchanged() -> None:
    data = {"t": dt.time(7, 32, 0, 999), "d": dt.date(1979, 5, 27), "s": "plain"}
    assert toml.dumps(data) == 't = 07:32:00.000999\nd = 1979-05-27\ns = "plain"\n'
