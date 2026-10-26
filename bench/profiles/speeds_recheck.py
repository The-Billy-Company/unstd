"""Balanced revision checks for persistent frozen function-latency failures."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import gc
from hashlib import sha256
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
from statistics import median
import sys
import time
from typing import TYPE_CHECKING, Any

from bench import speeds
from bench.core import timing
from bench.profiles.billy_serde import _retained
from bench.profiles.jsonx_throughput import _calibrate


if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


@dataclass
class Cohort:
    """Prepared functions, resources and their complete product namespace."""

    source: Path
    cases: dict[str, speeds.Case]
    modules: dict[str, ModuleType]
    files: dict[str, str]
    expected: dict[str, Any]


def _namespace() -> dict[str, ModuleType]:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    }


def _files(source: Path) -> dict[str, str]:
    return {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted((source / "unstd").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
    }


@contextmanager
def _bound(cohort: Cohort) -> Iterator[None]:
    saved, paths = _namespace(), sys.path.copy()
    prior = os.environ.get("PYTHONPATH")
    try:
        for name in saved:
            del sys.modules[name]
        sys.modules.update(cohort.modules)
        sys.path.insert(0, str(cohort.source))
        os.environ["PYTHONPATH"] = os.pathsep.join(
            (str(cohort.source), str(speeds.ROOT))
        )
        yield
    finally:
        cohort.modules = _namespace()
        for name in _namespace():
            del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = paths
        if prior is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = prior


def _prove(cohort: Cohort) -> dict[str, Any]:
    if _files(cohort.source) != cohort.files:
        speeds._invalid("paired recheck product source or native binary changed")
    environment = speeds.environment()
    if environment != cohort.expected["environment"]:
        speeds._invalid("paired recheck environment differs from original measurement")
    provenance = speeds.provenance()
    loaded = provenance["loaded_files"]
    if not isinstance(loaded, dict):
        speeds._invalid("paired recheck has invalid native provenance")
    for name, row in loaded.items():
        if (name == "unstd" or name.startswith("unstd.")) and not Path(
            row["path"]
        ).is_relative_to(cohort.source / "unstd"):
            speeds._invalid(f"paired recheck imported another product: {name}")
    for name, row in cohort.expected["provenance"]["loaded_files"].items():
        if name == "unstd" or name.startswith("unstd."):
            path = Path(row["path"])
            if not path.is_relative_to(cohort.source / "unstd") or (
                not path.is_file()
                or sha256(path.read_bytes()).hexdigest() != row["sha256"]
            ):
                speeds._invalid(f"paired recheck differs from frozen product: {name}")
    return {"environment": environment, "provenance": provenance, "files": cohort.files}


def _cohort(
    source: Path,
    expected: dict[str, Any],
    baseline: dict[str, Any],
    resources: ExitStack,
    label: str,
) -> Cohort:
    source = source.resolve()
    files = _files(source)
    if not files:
        speeds._invalid(
            "paired recheck source must contain the installed unstd package"
        )

    def load(
        _program: Path,
    ) -> tuple[Any, list[speeds.Case], dict[str, Any], dict[str, ModuleType]]:
        cases = []
        for provider in speeds._PROVIDERS:
            name = f"_speeds_recheck_{label}_{provider}"
            path = speeds.ROOT / f"bench/workloads/speed_cases_{provider}.py"
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                speeds._invalid(f"cannot load benchmark provider: {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            resources.callback(sys.modules.pop, name, None)
            spec.loader.exec_module(module)
            cases.extend(module.cases(resources, comparison=True))
        speeds.validate_cases(cases)
        by_name = {case.name: case for case in cases}
        for name, row in baseline["cases"].items():
            if name not in by_name or by_name[name].note != row["note"]:
                speeds._invalid(
                    f"paired recheck missing or changed frozen workload: {name}"
                )
        jsonx = importlib.import_module("unstd.serde.jsonx")
        return jsonx, cases, {"package_source": jsonx.__file__}, _namespace()

    _, cases, _, modules = _retained(Path(__file__), source, loader=load)
    return Cohort(source, {case.name: case for case in cases}, modules, files, expected)


def _pair(cohorts: dict[str, Cohort], name: str, rounds: int) -> list[dict[str, Any]]:
    for cohort in cohorts.values():
        with _bound(cohort):
            speeds.audit([cohort.cases[name]])
    enabled = gc.isenabled()
    gc.disable()
    try:
        return _pair_measured(cohorts, name, rounds)
    finally:
        if enabled:
            gc.enable()
        gc.collect()


def balanced_order(index: int) -> list[str]:
    """Counterbalance linear drift and first-arm effects within every round."""
    if type(index) is not int or index < 0:
        speeds._invalid("paired recheck requires an exact nonnegative round index")
    a, b = ("before", "current") if index % 2 == 0 else ("current", "before")
    return [a, b, b, a, b, a, a, b, b, a, a, b, a, b, b, a]


def round_measurements(pair: dict[str, Any]) -> dict[str, float]:
    """Validate and average every raw batch in a fully balanced round."""
    order = balanced_order(pair["round"])
    batches = pair["batches"]
    if pair["order"] != order or len(batches) != len(order):
        speeds._invalid("paired recheck lost its balanced batch order")
    values: dict[str, list[float]] = {
        name: []
        for name in ("before_ns", "current_ns", "before_cpu_ns", "current_cpu_ns")
    }
    count = pair["iterations"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        speeds._invalid("paired recheck requires positive equal iteration counts")
    for position, (label, batch) in enumerate(zip(order, batches, strict=True)):
        if (
            type(batch["position"]) is not int
            or type(batch["iterations"]) is not int
            or batch["position"] != position
            or batch["label"] != label
            or batch["iterations"] != count
        ):
            speeds._invalid("paired recheck changed source order or iteration counts")
        values[label + "_ns"].append(_positive(batch["ns"]))
        values[label + "_cpu_ns"].append(_positive(batch["cpu_ns"]))
    return {
        name: _positive(sum(samples) / len(samples)) for name, samples in values.items()
    }


def _pair_measured(
    cohorts: dict[str, Cohort], name: str, rounds: int
) -> list[dict[str, Any]]:
    calibrations = {}
    for label, cohort in cohorts.items():
        with _bound(cohort):
            calibrations[label] = _calibrate(cohort.cases[name].run)
    count = max(item[0] for item in calibrations.values())
    slowest = max(elapsed / iterations for iterations, elapsed in calibrations.values())
    count = min(count, max(1, int(timing._MAX_BATCH_NS / max(slowest, 1))))
    pairs = []
    for index in range(rounds):
        order = balanced_order(index)
        pair: dict[str, Any] = {
            "round": index,
            "order": order,
            "iterations": count,
            "batches": [],
        }
        for position, label in enumerate(order):
            with _bound(cohorts[label]):
                started = time.thread_time_ns()
                elapsed = timing._batch(cohorts[label].cases[name].run, count)
                cpu = time.thread_time_ns() - started
            pair["batches"].append(
                {
                    "position": position,
                    "label": label,
                    "iterations": count,
                    "ns": elapsed / count,
                    "cpu_ns": cpu / count,
                }
            )
        pair.update(round_measurements(pair))
        pair["current_over_before"] = pair["current_ns"] / pair["before_ns"]
        pairs.append(pair)
    return pairs


def _positive(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        speeds._invalid("paired recheck requires finite positive measurements")
    return float(value)


def decision(pairs: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    """Use the original row's exact ceiling factor with every matched sample."""
    if not pairs:
        speeds._invalid("paired recheck has no measured samples")
    limit = _positive(_positive(row["ceiling_ns"]) / _positive(row["measured_ns"]))
    ratios = []
    for pair in pairs:
        ratio = _positive(_positive(pair["current_ns"]) / _positive(pair["before_ns"]))
        if not math.isfinite(ratio):
            speeds._invalid("paired recheck ratio is not finite")
        ratios.append(ratio)
    ratio = median(ratios)
    return {
        "paired_limit": limit,
        "paired_current_over_before": ratio,
        "original_measured_ns": row["measured_ns"],
        "original_ceiling_ns": row["ceiling_ns"],
        "failed": ratio > limit,
        "pairs": pairs,
    }


def timeout_seconds(case_count: int, rounds: int) -> int:
    """Bound the complete worker while allowing every planned measured batch."""
    if (
        type(case_count) is not int
        or case_count < 1
        or type(rounds) is not int
        or rounds < 3
    ):
        speeds._invalid(
            "paired recheck requires positive cases and at least three rounds"
        )
    setup_seconds, maximum_seconds = 120, 8 * 60
    batch_ns = 16 * rounds * timing._MAX_BATCH_NS
    per_case_seconds = (batch_ns + 999_999_999) // 1_000_000_000 + 10
    return min(maximum_seconds, setup_seconds + per_case_seconds * case_count)


def worker(request: dict[str, Any]) -> dict[str, Any]:
    """Keep both original installed products and all fixture resources alive."""
    baseline, names = request["baseline"], request["names"]
    if baseline["workload_sources"] != speeds.workload_sources():
        speeds._invalid("paired recheck workload source differs from original freeze")
    if not names or any(name not in baseline["cases"] for name in names):
        speeds._invalid("paired recheck requires original frozen cases")
    budget = timeout_seconds(len(names), request["rounds"])
    if len(names) != len(set(names)):
        speeds._invalid("paired recheck repeats a frozen case")
    with speeds._pin_cpu(), ExitStack() as resources:
        cohorts = {}
        for label in ("before", "current"):
            expected = request[label]
            path = Path(expected["provenance"]["loaded_files"]["unstd"]["path"])
            cohorts[label] = _cohort(
                path.parent.parent, expected, baseline, resources, label
            )
        initial = {}
        for label, cohort in cohorts.items():
            with _bound(cohort):
                speeds.audit([cohort.cases[name] for name in names])
                initial[label] = _prove(cohort)
        results = {
            name: decision(
                _pair(cohorts, name, request["rounds"]), baseline["cases"][name]
            )
            for name in names
        }
        for label, cohort in cohorts.items():
            with _bound(cohort):
                speeds.audit([cohort.cases[name] for name in names])
                if _prove(cohort) != initial[label]:
                    speeds._invalid(
                        "paired recheck source, native cohort or environment drift"
                    )
        if baseline["workload_sources"] != speeds.workload_sources():
            speeds._invalid("paired recheck workload changed during timing")
        return {
            "cases": results,
            "products": initial,
            "rounds": request["rounds"],
            "protocol": "four counterbalanced ABBA/BAAB crossovers; all sixteen raw batches retained per round",
            "worker_timeout_seconds": budget,
        }


def main() -> None:
    """Run the bounded recheck in a fresh interpreter with a JSON request."""
    result = worker(json.load(sys.stdin))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
