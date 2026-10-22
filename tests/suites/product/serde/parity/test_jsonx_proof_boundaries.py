"""Native proof shortcuts retain snapshot, integer and recursion boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from unstd.serde import jsonx
from unstd.serde._json_encode import _UNSAFE, _snapshot


@pytest.mark.parametrize("container", [list, tuple])
@pytest.mark.parametrize("size", [8, 32, 63])
def test_integer_snapshot_is_independent_and_consumes_every_node(
    container: type[list[Any] | tuple[Any, ...]], size: int
) -> None:
    original = list(range(size))
    remaining = [64]
    snapshot = _snapshot(container(original), remaining, True)
    original.append(2**100)
    assert snapshot == list(range(size))
    assert remaining == [63 - size]


@pytest.mark.parametrize("position", [0, 7, 15])
@pytest.mark.parametrize("integer", [-(2**63) - 1, -(2**63), 2**64 - 1, 2**64])
def test_integer_array_boundaries_match_stdlib(position: int, integer: int) -> None:
    values = list(range(16))
    values[position] = integer
    expected = json.dumps(values)
    assert jsonx.dumps(values) == expected
    assert jsonx.dumpb(values) == json.dumps(values, separators=(",", ":")).encode()
    snapshot = _snapshot(values, [64], False)
    assert (snapshot is _UNSAFE) is not (-(2**63) <= integer < 2**64)


def test_integer_subclasses_are_not_compared_during_bulk_proof() -> None:
    class Integer(int):
        def __lt__(self, other: object) -> bool:
            pytest.fail("the proof compared an integer subclass")

        def __gt__(self, other: object) -> bool:
            pytest.fail("the proof compared an integer subclass")

    values = list(range(16))
    values[8] = Integer(8)
    assert _snapshot(values, [64], False) is _UNSAFE
    assert jsonx.dumps(values) == json.dumps(values)


@pytest.mark.parametrize("container", [list, tuple, dict])
def test_wide_containers_stay_outside_the_snapshot_budget(container: type[Any]) -> None:
    values = (
        container.fromkeys(map(str, range(64)), 1)
        if container is dict
        else container(range(64))
    )
    assert _snapshot(values, [64], False) is _UNSAFE


def test_compact_fallback_preserves_stdlib_recursion_headroom() -> None:
    source = textwrap.dedent(
        """
        import json
        import sys
        from unstd.serde import jsonx

        def reference(value):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

        def invoke(function, value, depth):
            if depth:
                return invoke(function, value, depth - 1)
            return function(value)

        def accepted(function, value, depth):
            try:
                invoke(function, value, depth)
            except RecursionError:
                return False
            return True

        violations = []
        available = jsonx._HAVE_ORJSON
        for enabled in (False, available):
            jsonx._HAVE_ORJSON = enabled
            for limit in (100, 140):
                sys.setrecursionlimit(limit)
                for value in (1.5, [1.5], list(range(1000)), [list(range(32))]):
                    for depth in range(limit - 16, limit):
                        if accepted(jsonx.dumpb, value, depth) and not accepted(
                            reference, value, depth
                        ):
                            violations.append((enabled, limit, depth))
        assert not violations, violations
        """
    )
    result = subprocess.run(  # noqa: S603 - isolated fixed-source recursion probe
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
