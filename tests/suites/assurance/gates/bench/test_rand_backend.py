"""Rand timings compare independently loaded bytes, outputs and RNG states."""

from __future__ import annotations

from pathlib import Path
import random
import sys

import pytest

from bench.profiles import rand_backend as profile
from unstd.rand import draw


def _engine():
    return random.Random(7)  # noqa: S311 — deterministic performance fixture


def test_saved_source_compiles_recorded_bytes_without_replacing_live_imports(tmp_path):
    path = tmp_path / "draw.py"
    path.write_bytes(Path(draw.__file__).read_bytes())
    before = dict(sys.modules)
    module, evidence = profile._load_source(path)
    assert module is not draw
    assert module.Generator(7).random() == draw.Generator(7).random()
    assert sys.modules["unstd.rand.draw"] is draw
    assert all(sys.modules[name] is item for name, item in before.items())
    profile._check_sources({"saved": evidence})
    path.write_text(path.read_text() + "\n# changed\n")
    with pytest.raises(RuntimeError, match="source changed"):
        profile._check_sources({"saved": evidence})


def test_hidden_extra_rng_draw_is_rejected_even_when_output_matches():
    current, before = _engine(), _engine()
    current.random()
    with pytest.raises(AssertionError, match="state consumption"):
        profile._check_pair(
            {"current": 1, "before": 1}, {"current": current, "before": before}
        )


def test_wrong_output_is_rejected_before_timing(monkeypatch):
    calls = iter((1, 2))
    case = profile.Case(
        "wrong",
        "stdlib",
        {"current": lambda: _engine(), "before": lambda: _engine()},
        lambda _engine: next(calls),
    )

    def forbidden(*_args):
        pytest.fail("invalid output entered timing")

    monkeypatch.setattr(profile, "_batch", forbidden)
    with pytest.raises(AssertionError, match="output changed"):
        profile._measure(case, rounds=3, minimum_ns=0)


def test_pair_order_alternates_and_shared_counts_preserve_streams(monkeypatch):
    calls = []

    def batch(call, operations):
        result = None
        for _ in range(operations):
            result = call()
        calls.append(operations)
        return {
            "operations": operations,
            "wall_ns": operations * 10,
            "thread_cpu_ns": operations * 10,
        }, result

    monkeypatch.setattr(profile, "_batch", batch)
    case = profile.Case(
        "paired",
        "stdlib",
        {"current": lambda: _engine(), "before": lambda: _engine()},
        lambda engine: engine.random(),
    )
    row = profile._measure(case, rounds=3, minimum_ns=100)
    assert calls == [1, 1, 10, 10, 10, 10, 10, 10]
    assert row["orders"] == [
        ["current", "before"],
        ["before", "current"],
        ["current", "before"],
    ]
    assert row["median_matched_before_over_current"]["wall_ns"] == 1
