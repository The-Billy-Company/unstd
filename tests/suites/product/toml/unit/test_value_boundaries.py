"""Date/time and numeric boundaries against hand-computed TOML 1.0.0 values.

Read expectations are spelled out from the TOML 1.0.0 specification, not from a
sample parse: offsets are whole minutes, fractional seconds beyond microseconds
truncate (never round), and integers/floats keep their sign and radix meaning.
Writer cases stay inside the dialect both ``tomlkit`` and ``tomllib`` share.
"""

from __future__ import annotations

import datetime as dt
import math
import tomllib

import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


def _tz(minutes: int) -> dt.timezone:
    return dt.timezone(dt.timedelta(minutes=minutes))


READS = [
    ("1979-05-27T07:32:00Z", dt.datetime(1979, 5, 27, 7, 32, tzinfo=_tz(0))),
    ("1979-05-27 07:32:00z", dt.datetime(1979, 5, 27, 7, 32, tzinfo=_tz(0))),
    ("1979-05-27t07:32:00-00:00", dt.datetime(1979, 5, 27, 7, 32, tzinfo=_tz(0))),
    ("1979-05-27T00:32:00+05:30", dt.datetime(1979, 5, 27, 0, 32, tzinfo=_tz(330))),
    ("1979-05-27T00:32:00-23:59", dt.datetime(1979, 5, 27, 0, 32, tzinfo=_tz(-1439))),
    (
        "1979-05-27T00:32:00.9999999-07:00",
        dt.datetime(1979, 5, 27, 0, 32, 0, 999999, tzinfo=_tz(-420)),
    ),
    ("1979-05-27T00:32:00.5", dt.datetime(1979, 5, 27, 0, 32, 0, 500000)),  # noqa: DTZ001 — TOML local datetime.
    ("0001-01-01T00:00:00", dt.datetime(1, 1, 1)),  # noqa: DTZ001 — TOML local datetime.
    ("9999-12-31T23:59:59.999999", dt.datetime(9999, 12, 31, 23, 59, 59, 999999)),  # noqa: DTZ001 — TOML local datetime.
    ("2000-02-29", dt.date(2000, 2, 29)),
    ("00:00:00.123", dt.time(0, 0, 0, 123000)),
    ("23:59:59.9999999", dt.time(23, 59, 59, 999999)),
    ("9_223_372_036_854_775_807", 2**63 - 1),
    ("-9223372036854775808", -(2**63)),
    ("+0", 0),
    ("0xDEAD_beef", 0xDEADBEEF),
    ("0o755", 0o755),
    ("0b1101", 13),
    ("6.626e-34", 6.626e-34),
    ("5E+22", 5e22),
    ("-inf", -math.inf),
    ("+inf", math.inf),
]


@pytest.mark.parametrize(("literal", "expected"), READS, ids=[r[0] for r in READS])
def test_reads_hand_computed_value(literal: str, expected: object) -> None:
    value = toml.loads(f"v = {literal}\n")["v"]
    assert type(value) is type(expected)
    assert value == expected
    if isinstance(expected, dt.datetime | dt.time):
        assert value.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize(
    ("literal", "sign"), [("0.0", 1.0), ("-0.0", -1.0), ("+0.0", 1.0)]
)
def test_signed_zero_keeps_its_sign(literal: str, sign: float) -> None:
    value = toml.loads(f"v = {literal}\n")["v"]
    assert value == 0.0
    assert math.copysign(1.0, value) == sign


@pytest.mark.parametrize("literal", ["nan", "+nan", "-nan"])
def test_nan_spellings_read_as_float_nan(literal: str) -> None:
    value = toml.loads(f"v = {literal}\n")["v"]
    assert isinstance(value, float)
    assert math.isnan(value)


def test_parse_float_hook_receives_exact_spelling() -> None:
    seen: list[str] = []

    def keep(text: str) -> str:
        seen.append(text)
        return text

    assert toml.loads("a = 0.1\nb = [1e3, -0.0]\n", parse_float=keep) == {
        "a": "0.1",
        "b": ["1e3", "-0.0"],
    }
    assert seen == ["0.1", "1e3", "-0.0"]


@pytest.mark.parametrize(
    "literal",
    [
        "1979-02-29",
        "1979-13-01",
        "1979-05-27T24:00:00",
        "1979-05-27T07:32:60Z",
        "1979-05-27T07:32:00+24:00",
        "24:32",
        "1_",
        "0x",
        "01",
        "-0x1",
        ".5",
        "5.",
        "1e",
        "infinity",
    ],
)
def test_out_of_range_or_misspelled_values_are_rejected(literal: str) -> None:
    with pytest.raises(toml.TOMLDecodeError):
        toml.loads(f"v = {literal}\n")


WRITES = [
    2**63 - 1,
    -(2**63),
    -0.0,
    math.inf,
    -math.inf,
    5e-324,
    1.7976931348623157e308,
    0.1,
    dt.datetime(1, 1, 1),  # noqa: DTZ001 — TOML local datetime round-trip.
    dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=_tz(-1439)),
    dt.datetime(2024, 2, 29, 12, 0, 0, 1, tzinfo=_tz(345)),
    dt.date(2000, 2, 29),
    dt.time(0, 0),
    dt.time(23, 59, 59, 999999),
]


@pytest.mark.parametrize("value", WRITES, ids=repr)
def test_writer_round_trips_boundaries_through_tomllib(value: object) -> None:
    require_backend("tomlkit")
    back = tomllib.loads(toml.dumps({"v": value}))["v"]
    assert type(back) is type(value)
    assert back == value
    if isinstance(value, float):
        assert math.copysign(1.0, back) == math.copysign(1.0, value)
    if isinstance(value, dt.datetime):
        assert back.utcoffset() == value.utcoffset()


def test_writer_round_trips_nan_through_tomllib() -> None:
    require_backend("tomlkit")
    assert math.isnan(tomllib.loads(toml.dumps({"v": math.nan}))["v"])
