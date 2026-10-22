"""``windowed`` with the default step read its input before the first pull.

The step-1 path staggered its ``tee`` views inside the call, consuming ``n - 1``
items from the source immediately. Abandoning the iterator lost those items from
a shared source, and a failing source raised from ``windowed(...)`` itself rather
than from iteration. Every other combinator here touches nothing until pulled.
"""

from __future__ import annotations

from itertools import count
from typing import TYPE_CHECKING

import pytest

from unstd.iters import windowed


if TYPE_CHECKING:
    from collections.abc import Iterator


class Counted:
    def __init__(self, source: Iterator[int]) -> None:
        self.source, self.pulled = source, 0

    def __iter__(self) -> Counted:
        return self

    def __next__(self) -> int:
        value = next(self.source)
        self.pulled += 1
        return value


@pytest.mark.parametrize("n", [2, 3, 5])
def test_default_step_reads_nothing_until_pulled(n: int) -> None:
    source = Counted(count())
    windows = windowed(source, n)
    assert source.pulled == 0
    assert next(windows) == tuple(range(n))
    assert source.pulled == n


def test_abandoned_windows_leave_a_shared_source_untouched() -> None:
    shared = iter(range(10))
    windowed(shared, 4)
    assert list(shared) == list(range(10))


def test_source_failure_surfaces_from_iteration_not_the_call() -> None:
    failure = RuntimeError("source refused")

    def refusing() -> Iterator[int]:
        raise failure
        yield

    windows = windowed(refusing(), 3)
    with pytest.raises(RuntimeError) as raised:
        next(windows)
    assert raised.value is failure
