"""Paired rand backend timings with exact output and engine-state comparisons.

Run directly to select saved/current ``draw.py`` sources before product imports.
Each adjacent pair starts independent equally seeded engines; validation and
seeding stay outside timing. Every batch consumes the same count on both sides.
The report retains raw wall/thread CPU samples and matched before/current ratios.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import partial
import hashlib
import inspect
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


SEED = 173


@dataclass(frozen=True)
class Case:
    """A workload and an engine factory, independent for every measured batch."""

    name: str
    backend: str
    factories: dict[str, Callable[[], Any]]
    operation: Callable[[Any], Any]
    draws: int | None = None


def _load_source(source: Path) -> tuple[ModuleType, dict[str, Any]]:
    """Compile the bytes recorded in evidence without importing live rand."""
    source = source.resolve()
    path = source if source.is_file() else source / "unstd/rand/draw.py"
    if not path.is_file():
        message = f"source must be draw.py or contain unstd/rand/draw.py: {source}"
        raise ValueError(message)
    data = path.read_bytes()
    name = "_unstd_rand_profile_" + hashlib.sha256(str(path).encode()).hexdigest()
    module = ModuleType(name)
    module.__file__ = str(path)
    # Compile bytes directly: a same-size edit cannot reuse stale bytecode.
    exec(compile(data, str(path), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 — explicitly selected local Python implementation
    signature_objects = {
        name: inspect.signature(getattr(module, name))
        for name in module.__all__
        if callable(getattr(module, name))
    }
    signature_objects.update(
        {
            f"Generator.{name}": inspect.signature(getattr(module.Generator, name))
            for name in module.Generator.__dict__
            if not name.startswith("_") and callable(getattr(module.Generator, name))
        }
    )
    signatures = {name: str(signature) for name, signature in signature_objects.items()}
    compatible = dict(signatures)
    additions = {}
    for name in ("ints", "Generator.ints"):
        signature = signature_objects[name]
        parameter = signature.parameters.get("seeds")
        if parameter is not None:
            if (
                parameter.kind != inspect.Parameter.KEYWORD_ONLY
                or parameter.default is not None
            ):
                message = f"unexpected nonoptional keyed seed argument in {name}"
                raise RuntimeError(message)
            compatible[name] = str(
                signature.replace(
                    parameters=[
                        value
                        for key, value in signature.parameters.items()
                        if key != "seeds"
                    ]
                )
            )
            additions[name] = str(parameter)
    helpers = {}
    keyed = getattr(module, "_keyed_ints", None)
    if keyed is not None:
        provider = sys.modules.get(keyed.__module__)
        provider_filename = getattr(provider, "__file__", None)
        if provider_filename is None:
            message = "keyed rand helper has no inspectable file"
            raise RuntimeError(message)
        helper_path = Path(provider_filename).resolve()
        native_binding = getattr(provider, "_native_seeded_ints", None)
        helpers["_keyed_ints"] = {
            "native_binding": {
                "module": native_binding.__module__,
                "name": native_binding.__name__,
            }
            if native_binding is not None
            else None,
            "module": keyed.__module__,
            "path": str(helper_path),
            "sha256": hashlib.sha256(helper_path.read_bytes()).hexdigest(),
        }
    native = {}
    for attribute in ("_native", "_native_shuffle"):
        binding = getattr(module, attribute, None)
        if binding is None:
            continue
        provider_name = (
            binding.__name__ if isinstance(binding, ModuleType) else binding.__module__
        )
        provider = (
            binding
            if isinstance(binding, ModuleType)
            else sys.modules.get(provider_name)
        )
        provider_filename = getattr(provider, "__file__", None)
        if provider_filename is None:
            message = f"native rand provider has no inspectable file: {provider_name}"
            raise RuntimeError(message)
        provider_path = Path(provider_filename).resolve()
        native[attribute] = {
            "module": provider_name,
            "name": binding.__name__,
            "path": str(provider_path),
            "sha256": hashlib.sha256(provider_path.read_bytes()).hexdigest(),
        }
    return module, {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "public_signatures": signatures,
        "compatible_public_signatures": compatible,
        "optional_public_additions": additions,
        "helpers": helpers,
        "have_numpy": module.HAVE_NUMPY,
        "native": native,
    }


def _check_sources(evidence: dict[str, dict[str, Any]]) -> None:
    """Require the measured source bytes and public signatures to stay fixed."""
    first = next(iter(evidence.values()))
    signatures = first.get("compatible_public_signatures", first["public_signatures"])
    for label, item in evidence.items():
        path = Path(item["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            message = f"rand source changed during measurement: {label}: {path}"
            raise RuntimeError(message)
        for native in (
            *item.get("native", {}).values(),
            *item.get("helpers", {}).values(),
        ):
            native_path = Path(native["path"])
            if hashlib.sha256(native_path.read_bytes()).hexdigest() != native["sha256"]:
                message = f"rand native binary changed during measurement: {label}"
                raise RuntimeError(message)
        if (
            item.get("compatible_public_signatures", item["public_signatures"])
            != signatures
        ):
            message = f"public rand signatures changed in {label}"
            raise RuntimeError(message)


def _canonical(value: Any) -> Any:
    """Compare exact array bytes, object identities, and complete engine states."""
    if hasattr(value, "dtype") and hasattr(value, "tobytes"):
        return (str(value.dtype), value.shape, value.tobytes())
    if callable(getattr(value, "profile_value", None)):
        return _canonical(value.profile_value())
    if hasattr(value, "_py"):
        return _state(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_canonical(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    return id(value)


def _state(engine: Any) -> Any:
    """The complete scalar and bulk engine state after a workload."""
    if callable(getattr(engine, "profile_state", None)):
        return engine.profile_state()
    if hasattr(engine, "_py"):
        numpy_engine = engine._gen
        return (
            engine.backend,
            engine._py.getstate(),
            _canonical(numpy_engine.bit_generator.state)
            if numpy_engine is not None
            else None,
        )
    if isinstance(engine, random.Random):
        return engine.getstate()
    return _canonical(engine.bit_generator.state)


def _check_pair(results: dict[str, Any], engines: dict[str, Any]) -> None:
    """Reject output changes or hidden changes in random-state consumption."""
    expected = next(iter(results))
    result, state = _canonical(results[expected]), _state(engines[expected])
    for label in results:
        if _canonical(results[label]) != result:
            message = f"seeded output changed in {label}"
            raise AssertionError(message)
        if _state(engines[label]) != state:
            message = f"random-state consumption changed in {label}"
            raise AssertionError(message)


def _floats(engine: Any, *, n: int, low: float = 0.0, high: float = 1.0) -> Any:
    return engine.floats(n, low=low, high=high)


def _ints(engine: Any, *, n: int, low: int, high: int) -> Any:
    return engine.ints(n, low, high)


def _choices(
    engine: Any, *, n: int, items: Sequence[Any], weights: Sequence[float] | None
) -> Any:
    return engine.choices(items, n, weights=weights)


def _construct(engine: Any, *, backend: str) -> Any:
    return type(engine)(SEED, backend=backend)


def _cases(modules: dict[str, ModuleType]) -> list[Case]:
    """Cover small/large bulk shapes plus scalar, seeding and direct controls."""
    numpy_support = {module.HAVE_NUMPY for module in modules.values()}
    if len(numpy_support) != 1:
        message = "sources must expose the same available backend set"
        raise RuntimeError(message)
    backends = ["stdlib", "numpy"] if True in numpy_support else ["stdlib"]
    population = tuple(object() for _ in range(32))
    large_population = tuple(object() for _ in range(8192))
    weights = tuple(float(item + 1) for item in range(32))
    cases = []
    for backend in backends:
        factories: dict[str, Callable[[], Any]] = {
            label: partial(module.Generator, SEED, backend=backend)
            for label, module in modules.items()
        }
        for n in (0, 1, 32, 4096):
            for name, low, high in (
                ("unit", 0.0, 1.0),
                ("scaled", -17.25, 92.5),
                ("constant", 2.5, 2.5),
            ):
                cases.append(
                    Case(
                        f"{backend}-floats-{name}-{n}",
                        backend,
                        factories,
                        partial(_floats, n=n, low=low, high=high),
                        n,
                    )
                )
            ranges = [("normal", -9, 100), ("one", 7, 8)]
            if backend == "stdlib":
                ranges.append(("big", -(2**120), 2**120))
            for name, low, high in ranges:
                cases.append(
                    Case(
                        f"{backend}-ints-{name}-{n}",
                        backend,
                        factories,
                        partial(_ints, n=n, low=low, high=high),
                        n,
                    )
                )
            for name, items, selected_weights in (
                ("uniform", population, None),
                ("weighted", population, weights),
                ("large-population", large_population, None),
            ):
                cases.append(
                    Case(
                        f"{backend}-choices-{name}-{n}",
                        backend,
                        factories,
                        partial(_choices, n=n, items=items, weights=selected_weights),
                        n,
                    )
                )
        for n in (65536,):
            cases.extend(
                [
                    Case(
                        f"{backend}-floats-unit-{n}",
                        backend,
                        factories,
                        partial(_floats, n=n),
                        n,
                    ),
                    Case(
                        f"{backend}-ints-normal-{n}",
                        backend,
                        factories,
                        partial(_ints, n=n, low=-9, high=100),
                        n,
                    ),
                ]
            )
        cases.extend(
            [
                Case(
                    f"{backend}-seed",
                    backend,
                    factories,
                    lambda engine: engine.seed(SEED),
                ),
                Case(
                    f"{backend}-construct",
                    backend,
                    factories,
                    # The factory belongs to the current engine's own loaded source.
                    partial(_construct, backend=backend),
                ),
                Case(
                    f"{backend}-random",
                    backend,
                    factories,
                    lambda engine: engine.random(),
                ),
                Case(
                    f"{backend}-randint",
                    backend,
                    factories,
                    lambda engine: engine.randint(-9, 100),
                ),
            ]
        )
    cases.append(
        Case(
            "control-stdlib-random",
            "stdlib direct control",
            {label: partial(random.Random, SEED) for label in modules},
            lambda engine: engine.random(),
        )
    )
    return cases


def _batch(call: Callable[[], Any], operations: int) -> tuple[dict[str, int], Any]:
    """Measure complete public calls, retaining the last result for checks."""
    result = None
    cpu_start = time.thread_time_ns()
    wall_start = time.perf_counter_ns()
    for _ in range(operations):
        result = call()
    wall_ns = time.perf_counter_ns() - wall_start
    cpu_ns = time.thread_time_ns() - cpu_start
    return {
        "operations": operations,
        "wall_ns": wall_ns,
        "thread_cpu_ns": cpu_ns,
    }, result


def _pair(
    case: Case, order: Sequence[str], operations: int
) -> dict[str, dict[str, int]]:
    """Start matched independent engines, then validate outside both timers."""
    engines = {label: case.factories[label]() for label in order}
    results, samples = {}, {}
    for label in order:
        samples[label], results[label] = _batch(
            partial(case.operation, engines[label]), operations
        )
    _check_pair(results, engines)
    return samples


def _measure(case: Case, *, rounds: int, minimum_ns: int) -> dict[str, Any]:
    """Calibrate one count for both sides and alternate adjacent pair order."""
    labels = list(case.factories)
    # A single untimed draw fails correctness before either measured batch.
    engines = {label: case.factories[label]() for label in labels}
    _check_pair(
        {label: case.operation(engine) for label, engine in engines.items()}, engines
    )
    calibration = _pair(case, labels, 1)
    operations = max(
        1,
        min(
            100_000,
            max(
                (minimum_ns + max(1, item["wall_ns"]) - 1) // max(1, item["wall_ns"])
                for item in calibration.values()
            ),
        ),
    )
    samples: dict[str, list[dict[str, int]]] = {label: [] for label in labels}
    orders = []
    for repetition in range(rounds):
        order = labels if repetition % 2 == 0 else labels[::-1]
        orders.append(order)
        pair = _pair(case, order, operations)
        for label in labels:
            samples[label].append(pair[label])
    medians = {
        label: {
            metric: statistics.median(
                sample[metric] / sample["operations"] for sample in values
            )
            for metric in ("wall_ns", "thread_cpu_ns")
        }
        for label, values in samples.items()
    }
    ratios = (
        {
            metric: statistics.median(
                before[metric] / current[metric]
                for before, current in zip(
                    samples["before"], samples["current"], strict=True
                )
            )
            for metric in ("wall_ns", "thread_cpu_ns")
        }
        if "before" in samples
        else {}
    )
    return {
        "name": case.name,
        "backend": case.backend,
        "draws_per_call": case.draws,
        "calibration": calibration,
        "target_batch_wall_ns": minimum_ns,
        "samples": samples,
        "orders": orders,
        "median_per_call_ns": medians,
        "median_matched_before_over_current": ratios,
        "exact_output_and_state_checks": True,
    }


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        message = "must be a positive integer"
        raise argparse.ArgumentTypeError(message)
    return number


def main(argv: list[str] | None = None) -> None:
    """Write a source-backed report after validating complete random streams."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=_positive, default=7)
    parser.add_argument("--batch-ms", type=_positive, default=5)
    parser.add_argument("--include", action="append", help="repeatable workload glob")
    args = parser.parse_args(argv)
    if args.rounds < 3:
        parser.error("paired comparisons require at least three rounds")
    modules, evidence = {}, {}
    for label, source in {
        "current": args.source,
        "before": args.compare_source,
    }.items():
        modules[label], evidence[label] = _load_source(source)
    _check_sources(evidence)
    cases = _cases(modules)
    if args.include:
        cases = [
            case
            for case in cases
            if any(fnmatchcase(case.name, pattern) for pattern in args.include)
        ]
    if not cases:
        parser.error("workload selection matched no cases")
    numpy = sys.modules.get("numpy")
    report = {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "numpy": getattr(numpy, "__version__", None),
        },
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": evidence,
        "rounds": args.rounds,
        "method": "adjacent alternating wall/thread CPU batches; independent equally seeded engines; identical operation counts; exact output and full engine state checks outside timing; GC left in its existing state",
        "workloads": [
            _measure(case, rounds=args.rounds, minimum_ns=args.batch_ms * 1_000_000)
            for case in cases
        ],
    }
    _check_sources(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in report["workloads"]:
        ratio = row["median_matched_before_over_current"]["wall_ns"]
        current = row["median_per_call_ns"]["current"]["wall_ns"]
        print(f"{row['name']}: {current:.1f} ns/call, before/current {ratio:.3f}x")


if __name__ == "__main__":
    main()
