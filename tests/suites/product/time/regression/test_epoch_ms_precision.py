"""``from_epoch_ms`` must land on whole milliseconds at every representable instant.

It used to compute ``fromtimestamp(ms / 1000)``. Past about 2^33 seconds a
double's spacing exceeds a microsecond, so the rounded float named a different
instant: ``253402300799999`` came back as ``…59.998993`` and ``10**13 + 1`` as
``….000999``. Expected values are calendar facts written by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from unstd.time import timeutil


def _platform_admits_milliseconds(ms: int, tz: timezone = UTC) -> bool:
    """Retain the host timestamp boundary while checking exact admitted values."""
    try:
        datetime.fromtimestamp(ms // 1000, UTC)
    except (OSError, OverflowError, ValueError) as error:
        expected_type, expected_args = type(error), error.args
        with pytest.raises(expected_type) as actual:
            timeutil.from_epoch_ms(ms, tz)
        assert type(actual.value) is expected_type
        assert actual.value.args == expected_args
        return False
    return True


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (253_402_300_799_999, datetime(9999, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC)),
        (10**13 + 1, datetime(2286, 11, 20, 17, 46, 40, 1_000, tzinfo=UTC)),
        (8_589_934_592_001, datetime(2242, 3, 16, 12, 56, 32, 1_000, tzinfo=UTC)),
        (-62_135_596_800_000, datetime(1, 1, 1, tzinfo=UTC)),
        (-1_001, datetime(1969, 12, 31, 23, 59, 58, 999_000, tzinfo=UTC)),
        (-1_000, datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)),
        (-999, datetime(1969, 12, 31, 23, 59, 59, 1_000, tzinfo=UTC)),
        (-1, datetime(1969, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC)),
        (0, datetime(1970, 1, 1, tzinfo=UTC)),
        (999, datetime(1970, 1, 1, microsecond=999_000, tzinfo=UTC)),
        (1_000, datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)),
        (1_001, datetime(1970, 1, 1, 0, 0, 1, 1_000, tzinfo=UTC)),
    ],
    ids=[
        "max",
        "2286",
        "2242",
        "min",
        "before-negative-second",
        "negative-second",
        "after-negative-second",
        "pre-epoch",
        "epoch",
        "before-positive-second",
        "positive-second",
        "after-positive-second",
    ],
)
def test_from_epoch_ms_is_exact(ms: int, expected: datetime) -> None:
    if not _platform_admits_milliseconds(ms):
        return
    result = timeutil.from_epoch_ms(ms)
    assert result == expected
    assert result.microsecond == expected.microsecond
    assert result.tzinfo is UTC


def test_exact_milliseconds_survive_zone_conversion() -> None:
    plus_one = timezone(timedelta(hours=1))
    if _platform_admits_milliseconds(253_402_297_199_999, plus_one):
        assert timeutil.from_epoch_ms(253_402_297_199_999, plus_one) == datetime(
            9999, 12, 31, 23, 59, 59, 999_000, tzinfo=plus_one
        )
    # 2026-11-01T06:45:00.007Z is the repeated 01:45 in New York, second pass.
    repeated = timeutil.from_epoch_ms(1_793_515_500_007, ZoneInfo("America/New_York"))
    assert (repeated.hour, repeated.minute, repeated.microsecond) == (1, 45, 7_000)
    assert repeated.fold == 1
    assert repeated.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize(("ms", "microsecond"), [(-1, 122_456), (1, 124_456)])
def test_fractional_zone_offset_preserves_the_millisecond_remainder(
    ms: int, microsecond: int
) -> None:
    offset = timezone(timedelta(seconds=561, microseconds=123_456))
    assert timeutil.from_epoch_ms(ms, offset) == datetime(
        1970, 1, 1, 0, 9, 21, microsecond, tzinfo=offset
    )


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0.25, datetime(1970, 1, 1, microsecond=250, tzinfo=UTC)),
        (-0.25, datetime(1969, 12, 31, 23, 59, 59, 999_750, tzinfo=UTC)),
        (1_000.5, datetime(1970, 1, 1, 0, 0, 1, 500, tzinfo=UTC)),
        (False, datetime(1970, 1, 1, tzinfo=UTC)),
        (True, datetime(1970, 1, 1, microsecond=1_000, tzinfo=UTC)),
    ],
)
def test_non_integer_numeric_inputs_keep_existing_rounding(
    ms: float | bool, expected: datetime
) -> None:
    assert timeutil.from_epoch_ms(ms) == expected  # type: ignore[arg-type]


def test_integer_subclass_keeps_its_divmod_contract() -> None:
    class SplitMilliseconds(int):
        def __divmod__(self, other: object) -> tuple[float, int]:  # type: ignore[override]
            assert other == 1_000
            return 1.25, 123

    assert timeutil.from_epoch_ms(SplitMilliseconds(1_000)) == datetime(
        1970, 1, 1, 0, 0, 1, 373_000, tzinfo=UTC
    )


@pytest.mark.parametrize("ms", [253_402_300_800_000, -62_135_596_800_001])
def test_out_of_range_preserves_the_platform_exception(ms: int) -> None:
    with pytest.raises((OSError, OverflowError, ValueError)) as reference:
        datetime.fromtimestamp(ms // 1000, UTC)
    with pytest.raises(type(reference.value)) as actual:
        timeutil.from_epoch_ms(ms)
    assert type(actual.value) is type(reference.value)
    assert actual.value.args == reference.value.args
