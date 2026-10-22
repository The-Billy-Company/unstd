"""Deterministic complete test partitions, without changing per-test budgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Sequence


def parse_shard(value: str) -> tuple[int, int]:
    """Read a one-based shard index and its common partition count."""
    try:
        index, count = map(int, value.split("/"))
    except ValueError as error:
        message = "shard must be INDEX/TOTAL with 1 <= INDEX <= TOTAL"
        raise ValueError(message) from error
    if not 1 <= index <= count:
        message = "shard must be INDEX/TOTAL with 1 <= INDEX <= TOTAL"
        raise ValueError(message)
    return index, count


def selected_nodeids(nodeids: Sequence[str], shard: tuple[int, int]) -> set[str]:
    """Partition sorted identities once; no test can overlap or disappear."""
    if len(nodeids) != len(set(nodeids)):
        message = "duplicate test identities cannot be sharded safely"
        raise pytest.UsageError(message)
    index, count = shard
    return set(sorted(nodeids)[index - 1 :: count])


def pytest_addoption(parser: pytest.Parser) -> None:
    """Expose the same partition selection to grouped and native test jobs."""
    parser.addoption(
        "--ci-shard",
        type=parse_shard,
        default=None,
        help="run one complete partition, INDEX/TOTAL (one based)",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Shard admitted tests after normal marker/keyword selection."""
    shard = config.getoption("--ci-shard")
    if shard is None:
        return
    selected = selected_nodeids([item.nodeid for item in items], shard)
    deselected = [item for item in items if item.nodeid not in selected]
    items[:] = [item for item in items if item.nodeid in selected]
    config.hook.pytest_deselected(items=deselected)
