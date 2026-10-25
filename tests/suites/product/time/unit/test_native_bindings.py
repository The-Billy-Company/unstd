"""Shared time primitives retain the native callables and strict decode errors."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as civil_time
import inspect
import time

import pytest

from unstd.time import timeutil


@pytest.mark.parametrize(
    ("name", "native"),
    [
        ("wall_ns", "time_ns"),
        ("perf_ns", "perf_counter_ns"),
        ("process_time", "process_time"),
        ("process_time_ns", "process_time_ns"),
        ("thread_time", "thread_time"),
        ("thread_time_ns", "thread_time_ns"),
        ("sleep", "sleep"),
        ("strftime", "strftime"),
        ("gmtime", "gmtime"),
        ("localtime", "localtime"),
    ],
)
def test_clocks_and_sleep_are_the_native_callables(name: str, native: str) -> None:
    """Identity proves there is no wrapper on successful or failing calls."""
    expected = getattr(time, native, None)
    if expected is None:
        assert not hasattr(timeutil, name)
        assert name not in timeutil.__all__
    else:
        assert getattr(timeutil, name) is expected
        assert name in timeutil.__all__


@pytest.mark.parametrize(
    ("name", "owner", "method"),
    [
        ("now", datetime, "now"),
        ("date_today", date, "today"),
        ("datetime_today", datetime, "today"),
        ("from_iso", datetime, "fromisoformat"),
        ("date_from_iso", date, "fromisoformat"),
        ("time_from_iso", civil_time, "fromisoformat"),
        ("from_timestamp", datetime, "fromtimestamp"),
        ("strptime", datetime, "strptime"),
        ("combine", datetime, "combine"),
    ],
)
def test_datetime_helpers_are_prebound_native_methods(name, owner, method) -> None:
    alias = getattr(timeutil, name)
    assert inspect.isbuiltin(alias)
    assert alias.__self__ is owner
    assert alias == getattr(owner, method)
    assert not hasattr(alias, "__code__")


@pytest.mark.parametrize(
    ("name", "native", "raw"),
    [
        ("from_iso", datetime.fromisoformat, "2026-01-01T03:04:05Z"),
        ("from_iso", datetime.fromisoformat, "20260101T030405"),
        ("from_iso", datetime.fromisoformat, "2026-W01-4T03:04:05+05:45"),
        ("from_iso", datetime.fromisoformat, "2026-01-01T03:04:05.123456-08:00"),
        ("from_iso", datetime.fromisoformat, "2026-01-01"),
        ("date_from_iso", date.fromisoformat, "2026-01-01"),
        ("date_from_iso", date.fromisoformat, "20260101"),
        ("date_from_iso", date.fromisoformat, "2026-W01-4"),
        ("time_from_iso", civil_time.fromisoformat, "03:04:05.123456"),
        ("time_from_iso", civil_time.fromisoformat, "T03:04:05Z"),
        ("time_from_iso", civil_time.fromisoformat, "030405+05:45"),
    ],
)
def test_strict_decoders_preserve_supported_shapes(name, native, raw) -> None:
    expected = native(raw)
    actual = getattr(timeutil, name)(raw)
    assert type(actual) is type(expected)
    assert actual == expected
    assert actual.isoformat() == expected.isoformat()


@pytest.mark.parametrize(
    ("name", "owner"),
    [("from_iso", datetime), ("date_from_iso", date), ("time_from_iso", civil_time)],
)
@pytest.mark.parametrize(
    "raw", ["", "not an instant", "2026-13-45T24:99:99", None, b"2026-01-01", 0]
)
def test_strict_decoders_preserve_exception_type_and_message(name, owner, raw) -> None:
    with pytest.raises(
        TypeError if not isinstance(raw, str) else ValueError
    ) as expected:
        owner.fromisoformat(raw)
    with pytest.raises(type(expected.value)) as actual:
        getattr(timeutil, name)(raw)
    assert actual.value.args == expected.value.args


def test_naive_and_zoned_now_keep_the_native_semantics() -> None:
    before = datetime.now()  # noqa: DTZ005 - the native local-naive contract is under test.
    observed = timeutil.now()
    after = datetime.now()  # noqa: DTZ005 - bounds use the same native local-naive contract.
    assert observed.tzinfo is None
    assert before <= observed <= after
    before_utc = datetime.now(UTC)
    zoned = timeutil.now(UTC)
    assert zoned.tzinfo is UTC
    assert before_utc <= zoned <= datetime.now(UTC)


def test_cpu_clocks_measure_the_native_cpu_clock() -> None:
    before = time.process_time_ns()
    observed = timeutil.process_time_ns()
    assert before <= observed <= time.process_time_ns()
    assert abs(timeutil.process_time() - observed / 1e9) < 1


def test_nanosecond_clocks_keep_integer_precision() -> None:
    assert type(timeutil.wall_ns()) is int
    assert type(timeutil.perf_ns()) is int
    assert abs(timeutil.wall_ns() / 1e9 - time.time()) < 1


@pytest.mark.parametrize(
    "seconds",
    [-1_793_515_500.125, -1.5, 0, 1.5, 1_793_515_500.125, float("inf"), float("nan")],
)
@pytest.mark.parametrize("zone", [None, UTC])
def test_native_epoch_decoding_retains_naive_and_explicit_zones(seconds, zone) -> None:
    try:
        expected = datetime.fromtimestamp(seconds, zone)
    except (OSError, OverflowError, ValueError) as error:
        expected_error = error
    else:
        expected_error = None
    if expected_error is not None:
        with pytest.raises(type(expected_error)) as actual_error:
            timeutil.from_timestamp(seconds, zone)
        assert actual_error.value.args == expected_error.args
        return
    actual = timeutil.from_timestamp(seconds, zone)
    assert actual == expected
    assert actual.tzinfo is expected.tzinfo
    assert actual.fold == expected.fold
    assert actual.microsecond == expected.microsecond


def test_sleep_keeps_the_native_rejection() -> None:
    with pytest.raises(ValueError, match="non-negative") as expected:
        time.sleep(-1)
    with pytest.raises(ValueError, match="non-negative") as actual:
        timeutil.sleep(-1)
    assert actual.value.args == expected.value.args


def test_strict_and_permissive_parsing_remain_distinct() -> None:
    raw = "invalid"
    assert timeutil.parse_iso(raw) is None
    with pytest.raises(ValueError, match="isoformat"):
        timeutil.from_iso(raw)
    assert timeutil.parse_date(raw) is None
    with pytest.raises(ValueError, match="isoformat"):
        timeutil.date_from_iso(raw)
    assert timeutil.now(UTC).utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "seconds", [-1.5, 0, 1.5, 1_793_515_500.125, float("inf"), float("nan")]
)
@pytest.mark.parametrize("name", ["gmtime", "localtime"])
def test_time_tuples_preserve_native_calendar_and_dst_fields(name, seconds) -> None:
    try:
        native = getattr(time, name)(seconds)
    except (OSError, OverflowError, ValueError) as error:
        expected_error = error
    else:
        expected_error = None
    if expected_error is not None:
        with pytest.raises(type(expected_error)) as actual_error:
            getattr(timeutil, name)(seconds)
        assert actual_error.value.args == expected_error.args
        return
    actual = getattr(timeutil, name)(seconds)
    assert type(actual) is type(native)
    assert actual == native
    assert actual.tm_zone == native.tm_zone
    assert actual.tm_gmtoff == native.tm_gmtoff


@pytest.mark.parametrize(
    "format", ["%Y-%m-%dT%H:%M:%SZ", "%A %B %d %H:%M:%S", "%j %w %U %W", "東京 %Y café"]
)
def test_tuple_formatting_retains_native_directives_and_unicode(format) -> None:
    prepared = time.gmtime(1_767_225_600)
    assert timeutil.strftime(format, prepared) == time.strftime(format, prepared)


@pytest.mark.parametrize(
    ("raw", "format"),
    [
        ("2026-01-01T03:04:05Z", "%Y-%m-%dT%H:%M:%SZ"),
        ("20260101030405", "%Y%m%d%H%M%S"),
        ("2026-01-01 03:04:05.123456 +0545", "%Y-%m-%d %H:%M:%S.%f %z"),
        ("2024 060", "%Y %j"),
    ],
)
def test_explicit_format_parsing_preserves_native_results(raw, format) -> None:
    expected = datetime.strptime(raw, format)  # noqa: DTZ007 - native naive and offset contracts are under test.
    actual = timeutil.strptime(raw, format)
    assert actual == expected
    assert actual.tzinfo == expected.tzinfo
    assert actual.fold == expected.fold


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("strptime", ("2026-02-30", "%Y-%m-%d")),
        ("strptime", ("not a date", "%Y-%m-%d")),
        ("strptime", (None, "%Y-%m-%d")),
        ("strftime", ("%Y", ())),
        ("gmtime", (float("nan"),)),
        ("localtime", (float("nan"),)),
        ("combine", ("not a date", civil_time())),
    ],
)
def test_calendar_bindings_preserve_native_exception_type_and_message(
    name, args
) -> None:
    owner = datetime if name in {"strptime", "combine"} else time
    with pytest.raises((TypeError, ValueError, OverflowError)) as expected:
        getattr(owner, name)(*args)
    with pytest.raises(type(expected.value)) as actual:
        getattr(timeutil, name)(*args)
    assert actual.value.args == expected.value.args


@pytest.mark.parametrize("fold", [0, 1])
def test_combine_retains_time_zone_fold_and_explicit_override(fold) -> None:
    day = date(2026, 11, 1)
    clock = civil_time(1, 30, 4, 123456, tzinfo=UTC, fold=fold)
    actual = timeutil.combine(day, clock)
    assert actual == datetime.combine(day, clock)
    assert actual.tzinfo is UTC
    assert actual.fold == fold
    zone = UTC if fold else None
    overridden = timeutil.combine(day, clock, tzinfo=zone)
    assert overridden == datetime.combine(day, clock, tzinfo=zone)
    assert overridden.tzinfo is zone
    assert overridden.fold == fold
