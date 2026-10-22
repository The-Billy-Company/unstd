"""Every public workload must stay within its frozen worker latency ceiling."""

from __future__ import annotations

import os


def test_every_public_workload_meets_its_frozen_speed() -> None:
    # Delay optional backend imports until the explicitly selected perf lane.
    # Missing dependencies must fail this run instead of skipping its evidence.
    from bench import speeds

    arguments = ["verify"]
    if baseline := os.environ.get("UNSTD_SPEED_BASELINE"):
        arguments.extend(("--baseline", baseline))
    assert speeds.main(arguments) == 0
