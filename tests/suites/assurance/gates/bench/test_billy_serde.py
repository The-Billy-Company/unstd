"""Source-isolated workloads and paired ratios cannot quietly compare one build twice."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

from bench.profiles import billy_serde


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_retained_workloads_keep_distinct_module_globals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sys.modules.get("unstd")

    def workloads(_benchmark: Path):
        from unstd.serde import jsonx

        return None, [jsonx.value], {"package_source": jsonx.__file__}, None

    monkeypatch.setattr(billy_serde, "_workloads", workloads)
    retained = []
    for label in ("before", "after"):
        source = tmp_path / label
        package = source / "unstd/serde"
        package.mkdir(parents=True)
        (source / "unstd/__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "jsonx.py").write_text(
            f"LABEL = {label!r}\ndef value(): return LABEL\n"
        )
        retained.append(billy_serde._retained(tmp_path / "unused", source)[1][0])
    assert [call() for call in retained] == ["before", "after"]
    assert sys.modules.get("unstd") is original


def test_paired_gate_uses_neighboring_batch_ratios() -> None:
    shared = {
        "identical_output": True,
        "required_native": None,
        "native_calls": {},
        "backend": "reference",
    }
    reports = {
        "before": [{"cases": {"sample": {**shared, "cpu_ns": 100}}}],
        "after": [
            {
                "cases": {
                    "sample": {
                        **shared,
                        "cpu_ns": 110,
                        "paired_ratios": [0.9, 0.9, 1.1],
                    }
                }
            }
        ],
    }
    cases, failures = billy_serde._summarize(reports, 1.0)
    assert failures == []
    assert cases["sample"]["after_over_before"] == 0.9
    assert cases["sample"]["after_faster_samples"] == 2


def test_native_dispatch_readiness_is_observed_after_profile_and_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"ready": True}

    def native_calls(_operation):
        state["ready"] = False
        return {"msgspec": 1}

    def actual():
        state["ready"] = True
        return {"id": 1}

    monkeypatch.setattr(billy_serde, "_native_calls", native_calls)
    case = SimpleNamespace(
        name="json-load-request",
        reference=lambda: {"id": 1},
        actual=actual,
        native="msgspec",
        fallback="stdlib",
    )
    result = billy_serde._verify(case, {"loads_ready": lambda: state["ready"]})
    assert result["identical_output"]
    assert result["native_calls"] == {"msgspec": 1}
    assert not result["native_function_dispatch_after_profile"]
    assert result["native_function_dispatch_ready"]


def test_native_spacing_probe_requires_exact_bytes_not_entry_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = json.dumps({"quoted:key": ['comma, quote" slash\\', {}]})
    monkeypatch.setattr(
        billy_serde,
        "_native_calls",
        lambda _operation: {"unstd.serde._json_native": 1},
    )
    for name in ("json-default-source", "json-default-source/direct"):
        case = SimpleNamespace(
            name=name,
            reference=lambda: expected,
            actual=lambda: expected,
            native=None,
            fallback="stdlib",
        )
        for encoded in (
            NotImplemented,
            expected,
            json.dumps(json.loads(expected), separators=(",", ":")).encode(),
            expected.encode(),
        ):
            result = billy_serde._verify(case, {"spacing": lambda value=encoded: value})
            assert result["identical_output"]
            assert result["native_calls"] == {"unstd.serde._json_native": 1}
            assert result["native_spacing_accepts_fixture"] is (
                type(encoded) is bytes and encoded == expected.encode()
            )


def test_paired_gate_rejects_declined_native_spacing() -> None:
    shared = {
        "identical_output": True,
        "required_native": None,
        "native_calls": {"unstd.serde._json_native": 1},
        "backend": "unstd.serde._json_native",
        "cpu_ns": 100,
    }
    reports = {
        "before": [{"cases": {"json-default-source": shared}}],
        "after": [
            {
                "cases": {
                    "json-default-source": {
                        **shared,
                        "native_spacing_accepts_fixture": False,
                    }
                }
            }
        ],
    }
    _, failures = billy_serde._summarize(reports, None)
    assert failures == [
        "json-default-source: native spacing rejected the required fixture"
    ]
