"""Alternate retained original/current proc modules within one interpreter.

This experiment reuses the frozen Consumer fixtures, exact output checks and route
proofs. Each workload alternates baseline/native order for every paired batch;
module namespaces remain separate and global import state is restored.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gc
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
import time
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "bench/profiles/consumer_proc.py"
SPEC = importlib.util.spec_from_file_location("_consumer_proc_paired_fixtures", HARNESS)
if SPEC is None or SPEC.loader is None:
    message = "the frozen process fixture harness is unavailable"
    raise RuntimeError(message)
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
SPEC.loader.exec_module(profile)
CASES = (
    "git-revision-cwd",
    "git-revision-no-cwd",
    "git-show-1mib",
    "git-show-16mib",
    "pkg-config-libdir",
    "qualification-log-shape",
)


def _modules() -> dict[str, ModuleType]:
    """Snapshot only the product namespace whose absolute imports we isolate."""
    return {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "unstd" or name.startswith("unstd.")
    }


def _load(source: Path) -> dict[str, ModuleType]:
    """Retain independent module globals while preserving the caller's imports."""
    source = source.resolve()
    saved = _modules()
    try:
        for name in saved:
            del sys.modules[name]
        root = ModuleType("unstd")
        root.__path__ = [str(source / "unstd")]
        sys.modules["unstd"] = root
        proc = importlib.import_module("unstd.proc")
        captured = _modules()
        for name, module in captured.items():
            filename = getattr(module, "__file__", None)
            if name != "unstd" and (
                filename is None
                or not Path(filename).resolve().is_relative_to(source / "unstd/proc")
            ):
                message = f"isolated module escaped selected source: {name}"
                raise RuntimeError(message)
        backend = (
            captured.get("unstd.proc.backend")
            or captured["unstd.proc._internal.backend"]
        )
        if proc.run.__globals__["_Popen"] is not backend.Popen:
            message = "proc run is detached from its own retained backend"
            raise RuntimeError(message)
        if (
            Path(proc.run.__code__.co_filename).resolve()
            != source / "unstd/proc/__init__.py"
        ):
            message = "retained run function came from the wrong source"
            raise RuntimeError(message)
        return captured
    finally:
        for name in _modules():
            del sys.modules[name]
        sys.modules.update(saved)


def _batch(proc: ModuleType, case: Any) -> dict[str, int | float]:
    """Measure complete calls, validating and freeing outputs before the next side."""
    call = getattr(proc, case.api)
    enabled = gc.isenabled()
    gc.disable()
    try:
        cpu_start = time.process_time_ns()
        wall_start = time.perf_counter_ns()
        results = [
            call(case.argv, **(case.options or {})) for _ in range(case.iterations)
        ]
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.process_time_ns() - cpu_start
    finally:
        if enabled:
            gc.enable()
    for result in results:
        profile._check(result, case)
        if case.api == "run" and type(result) is not proc.CompletedProcess:
            message = "a retained proc returned another namespace's result class"
            raise RuntimeError(message)
    return {
        "wall_ns": wall_ns,
        "parent_cpu_ns": cpu_ns,
        "iterations": case.iterations,
        "wall_ns_per_call": wall_ns / case.iterations,
        "parent_cpu_ns_per_call": cpu_ns / case.iterations,
    }


def _native_proxy(backend: ModuleType, mode: str) -> ModuleType:
    """Keep control and profiler mutations local to one retained backend."""
    native = getattr(backend, "_native", None)
    if native is None:
        message = "current proc did not load its native extension"
        raise RuntimeError(message)
    proxy = ModuleType("_paired_proc_native")
    vars(proxy)["__file__"] = native.__file__

    def decline(*args: Any, **kwargs: Any) -> Any:
        return NotImplemented

    for operation in ("spawn", "communicate"):
        disabled = (mode == "io" and operation == "spawn") or (
            mode == "spawn" and operation == "communicate"
        )
        setattr(proxy, operation, decline if disabled else getattr(native, operation))
    vars(backend)["_native"] = proxy
    if mode in {"spawn", "io"}:
        vars(backend)["_SPAWN_NATIVE"] = mode == "spawn"
    return proxy


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Check provenance and successful native routes before paired timing."""
    sources = {
        "baseline": args.baseline_source.resolve(),
        "native": args.source.resolve(),
    }
    hashes = {name: profile._source_hashes(source) for name, source in sources.items()}
    harness_hash = profile._sha(HARNESS.read_bytes())
    own_hash = profile._sha(Path(__file__).read_bytes())
    loaded = {name: _load(source) for name, source in sources.items()}
    backends = {
        name: modules.get("unstd.proc.backend")
        or modules["unstd.proc._internal.backend"]
        for name, modules in loaded.items()
    }
    if (
        getattr(backends["baseline"], "_native", None) is not None
        or backends["baseline"].Popen is backends["native"].Popen
    ):
        message = "original proc is native-enabled or shares the current backend"
        raise RuntimeError(message)
    native = _native_proxy(backends["native"], args.mode)
    spawn_enabled = bool(getattr(backends["native"], "_SPAWN_NATIVE", True))
    samples: dict[str, Any] = {}
    prior_environment = dict(os.environ)
    try:
        with TemporaryDirectory(prefix="proc-paired-fixture-") as temporary:
            fixture = profile._prepare_fixture(Path(temporary))
            environment = profile._environment(Path(temporary))
            os.environ.clear()
            os.environ.update(environment)
            cases, skipped = profile._workloads(fixture)
            selected = (
                {case.name for case in cases} if args.all else set(args.case or CASES)
            )
            missing = selected - {case.name for case in cases}
            if missing:
                message = (
                    f"selected workloads unavailable: {sorted(missing)}; {skipped}"
                )
                raise RuntimeError(message)
            for index, case in enumerate(
                case for case in cases if case.name in selected
            ):
                profiles = {}
                for side, modules in loaded.items():
                    operation = getattr(modules["unstd.proc"], case.api)
                    profiles[side] = profile._profile(
                        operation, case, native if side == "native" else None
                    )
                profile._verify_routes(
                    args.mode,
                    profiles["native"]["native_routes"],
                    require_native=True,
                    case=case.name,
                    spawn_enabled=spawn_enabled,
                    stdin_text=(case.options or {}).get("stdin_text") is not None,
                )
                pairs = []
                for repetition in range(args.rounds):
                    order = (
                        ["baseline", "native"]
                        if (index + repetition) % 2 == 0
                        else ["native", "baseline"]
                    )
                    pair = {
                        side: _batch(loaded[side]["unstd.proc"], case) for side in order
                    }
                    pairs.append({"order": order, **pair})
                samples[case.name] = {
                    "profiles": profiles,
                    "pairs": pairs,
                    "scope": case.scope,
                    "receipts": case.receipts,
                    "expected_native_kernels": profile._expected_native(
                        args.mode,
                        spawn_enabled=spawn_enabled,
                        stdin_text=(case.options or {}).get("stdin_text") is not None,
                    ),
                    "native_claim_eligible": bool(
                        profile._expected_native(
                            args.mode,
                            spawn_enabled=spawn_enabled,
                            stdin_text=(case.options or {}).get("stdin_text")
                            is not None,
                        )
                    ),
                }
                if (
                    case.options
                    and case.options.get("stdin_text") is not None
                    and not samples[case.name]["native_claim_eligible"]
                ):
                    samples[case.name]["native_coverage_note"] = (
                        "unaccelerated stdin stress; stdlib fallback, no native gain claimed"
                    )
    finally:
        os.environ.clear()
        os.environ.update(prior_environment)
    if (
        any(
            profile._source_hashes(source) != hashes[side]
            for side, source in sources.items()
        )
        or harness_hash != profile._sha(HARNESS.read_bytes())
        or own_hash != profile._sha(Path(__file__).read_bytes())
    ):
        message = "source or experiment changed during paired measurement"
        raise RuntimeError(message)
    signatures = [
        {
            name: str(inspect.signature(getattr(modules["unstd.proc"], name)))
            for name in ("run", "capture", "run_ok")
        }
        for modules in loaded.values()
    ]
    if signatures[0] != signatures[1]:
        message = "public proc signatures differ"
        raise RuntimeError(message)
    summary = {}
    for case, evidence in samples.items():
        row = {}
        for metric in ("wall_ns_per_call", "parent_cpu_ns_per_call"):
            before = [pair["baseline"][metric] for pair in evidence["pairs"]]
            after = [pair["native"][metric] for pair in evidence["pairs"]]
            row[metric] = {
                "baseline_median": statistics.median(before),
                "native_median": statistics.median(after),
                "median_speedup": statistics.median(before) / statistics.median(after),
                "paired_speedups": [a / b for a, b in zip(before, after, strict=True)],
            }
        summary[case] = row
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "native_mode": args.mode,
        "native_spawn_policy": spawn_enabled,
        "measurement": "retained separate module globals; each workload alternates each batch; child CPU excluded",
        "experiment_sha256": own_hash,
        "fixture_harness_sha256": harness_hash,
        "source_hashes": hashes,
        "module_paths": {
            side: {
                name: getattr(module, "__file__", None)
                for name, module in modules.items()
            }
            for side, modules in loaded.items()
        },
        "public_signatures": signatures[0],
        "fixture": fixture,
        "summary": summary,
        "samples": samples,
    }


def main() -> None:
    """Run a small source-isolated paired experiment and retain every sample."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "src")
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument(
        "--mode", choices=("candidate", "spawn", "io"), default="candidate"
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--all", action="store_true", help="measure every available frozen workload"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    report = run(args)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for case, row in report["summary"].items():
        wall, cpu = row["wall_ns_per_call"], row["parent_cpu_ns_per_call"]
        print(
            f"{case}: {wall['baseline_median'] / 1e6:.3f}->{wall['native_median'] / 1e6:.3f}ms ({wall['median_speedup']:.3f}x), parent CPU {cpu['median_speedup']:.3f}x"
        )


if __name__ == "__main__":
    main()
