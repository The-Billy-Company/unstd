"""Address collisions retain exact closed-graph cycle and alias behavior."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from unstd.clone import structural


native = pytest.importorskip("unstd.clone._clone_native")


def _colliding_lists(count: int) -> tuple[list[list[Any]], list[list[Any]]]:
    """Hold real allocations with equal active-address filter positions."""
    pool: list[list[Any]] = [[] for _ in range(count * 256)]
    groups: dict[int, list[list[Any]]] = {}
    for value in pool:
        address = id(value) >> 4
        group = groups.setdefault((address ^ (address >> 4)) & 255, [])
        group.append(value)
        if len(group) == count:
            return pool, group
    message = "pigeonhole allocation pool must supply address collisions"
    raise AssertionError(message)


@pytest.mark.parametrize("external_memo", [False, True])
def test_closed_graph_address_collisions_are_distinct_nodes(
    external_memo: bool,
) -> None:
    pool, nodes = _colliding_lists(12)
    for index, value in enumerate(nodes[:-1]):
        value.extend([index, nodes[index + 1]])
    nodes[-1].append("leaf")
    source = [nodes[0], nodes[5], nodes[0]]
    expected = copy.deepcopy(source)

    actual = (
        native.clone_graph(source, {}) if external_memo else native.clone_graph(source)
    )

    assert actual == expected
    assert actual is not source
    assert actual[0] is actual[2]
    cursor = actual[0]
    for _ in range(5):
        cursor = cursor[1]
    assert cursor is actual[1]
    cursor = actual[0]
    for index, original in enumerate(nodes):
        assert cursor is not original
        if index + 1 < len(nodes):
            cursor = cursor[1]
    assert len(pool) == 12 * 256


@pytest.mark.parametrize("external_memo", [False, True])
def test_closed_graph_cycle_after_completed_address_collision(
    external_memo: bool,
) -> None:
    pool, (source, completed) = _colliding_lists(2)
    completed.append("complete sibling")
    source.extend([completed, [source]])

    arguments = (source, {}) if external_memo else (source,)
    with pytest.raises(RecursionError, match="cyclic clone graph"):
        native.clone_graph(*arguments)

    actual = structural.deep(source)
    assert actual is not source
    assert actual[0] == completed
    assert actual[0] is not completed
    assert actual[1][0] is actual
    assert len(pool) == 2 * 256
