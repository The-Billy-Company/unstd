"""Exact immutable temporal sequences and controls for public clone profiles."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from bench.profiles.clone_cpu import Workload


if TYPE_CHECKING:
    from collections.abc import Callable


type Temporal = dt.date | dt.datetime | dt.time
type Scalar = bool | int | float | str | bytes | None
type Tree = Temporal | Scalar | list[Tree] | tuple[Tree, ...]


def _matches(actual: object, source: Tree, *, stdlib: bool) -> bool:
    """Check exact values, fresh containers and the immutable-sharing contract."""
    if type(actual) is not type(source):
        return False
    if isinstance(source, list | tuple):
        if not isinstance(actual, list | tuple) or actual is source:
            return False
        return len(actual) == len(source) and all(
            _matches(child, old, stdlib=stdlib)
            for child, old in zip(actual, source, strict=True)
        )
    if actual != source:
        return False
    if isinstance(source, dt.datetime | dt.time):
        if not isinstance(actual, dt.datetime | dt.time):
            return False
        if actual.fold != source.fold or actual.tzinfo is not source.tzinfo:
            return False
    return stdlib or actual is source


def _case(name: str, source: Tree) -> Workload:
    def check(result: object, stdlib: bool) -> bool:
        return _matches(result, source, stdlib=stdlib)

    return Workload(name, source, check)


def _dates(count: int) -> list[Tree]:
    first = dt.date(2026, 1, 1).toordinal()
    return [dt.date.fromordinal(first + index) for index in range(count)]


def workloads() -> list[Workload]:
    """Keep ten homogeneous sequences beside four routes that need controls."""
    factories: tuple[tuple[str, Callable[[int], Temporal]], ...] = (
        (
            "date",
            lambda index: dt.date.fromordinal(dt.date(2026, 1, 1).toordinal() + index),
        ),
        (
            "datetime-naive",
            lambda index: dt.datetime(
                2026, 1, 1, microsecond=index, tzinfo=dt.UTC
            ).replace(tzinfo=None),
        ),
        (
            "datetime-utc",
            lambda index: dt.datetime(2026, 1, 1, microsecond=index, tzinfo=dt.UTC),
        ),
        ("time-naive", lambda index: dt.time(12, 0, 0, index)),
        ("time-utc", lambda index: dt.time(12, 0, 0, index, tzinfo=dt.UTC)),
    )
    cases = [
        _case(
            f"temporal-{name}-{count}",
            [factory(index) for index in range(count)],
        )
        for name, factory in factories
        for count in (1000, 10000)
    ]
    mixed: list[Tree] = [
        value
        for index, date in enumerate(_dates(250))
        for value in (date, index, "leaf", None)
    ]
    cases.extend(
        (
            _case("temporal-mixed-scalars-1000", mixed),
            _case("temporal-tuple-date-1000", tuple(_dates(1000))),
            _case("temporal-nested-date-1000", [[date] for date in _dates(1000)]),
            _case("temporal-scalar-first-1000", [0, *_dates(999)]),
        )
    )
    return cases
