"""Exact captured Billy rand consumers, with paired timing and RNG cost profiles.

This measures synchronous consumer slices over labelled synthetic inputs. It does
not include LLM/network/database work or claim whole-application speedups. Source
receipts pin the original consumer functions; all application-specific fixtures
and controls live here, outside the rand implementation.
"""

from __future__ import annotations

import argparse
import ast
import copy
import cProfile
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import platform
import pstats
import random
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.profiles import rand_backend as timing


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


class _Kind(Enum):
    TOOL_CALL = "tool_call"
    TOOL_CHAIN = "tool_chain"
    OTHER = "other"


@dataclass
class _Example:
    """Projection of attributes read by the captured consumer functions."""

    kind: _Kind
    tool_name: str
    messages: list[dict[str, Any]]
    mask_seed: int = 0

    def profile_value(self) -> Any:
        """The observable fields, normalized outside timing for output checks."""
        return self.kind.value, self.tool_name, self.messages, self.mask_seed


def _consumer_ast_hash(node: ast.AST) -> str:
    """Retain receipt formatting across Python's empty-field dump change."""
    normalized = copy.deepcopy(node)
    for child in ast.walk(normalized):
        for field, value in ast.iter_fields(child):
            if isinstance(value, list) and not value:
                delattr(child, field)
    return hashlib.sha256(
        ast.dump(normalized, include_attributes=False).encode()
    ).hexdigest()


def _load_consumers(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check every captured symbol AST against its original source receipt."""
    raw = path.read_bytes()
    captured: dict[str, Any] = json.loads(raw)
    if captured["version"] != 1:
        message = "unsupported Billy rand fixture version"
        raise ValueError(message)
    trees = {}
    for name, item in captured["consumers"].items():
        tree = ast.parse(item["source"], filename=item["source_path"])
        symbols = {
            node.name: _consumer_ast_hash(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        if symbols != {
            symbol: receipt["ast_sha256"] for symbol, receipt in item["symbols"].items()
        }:
            message = f"captured consumer AST disagrees with receipt: {name}"
            raise ValueError(message)
        # Imports bind to the selected rand source; symbol bodies remain exact.
        tree.body = [
            node
            for node in tree.body
            if not isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        trees[name] = compile(tree, item["source_path"], "exec")
    evidence = {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "repository": captured["repository"],
        "commit": captured["commit"],
        "input_provenance": captured["input_provenance"],
        "consumers": {
            name: {key: value for key, value in item.items() if key != "source"}
            for name, item in captured["consumers"].items()
        },
    }
    return trees, evidence


class Context:
    """A selected rand implementation plus independent captured consumer globals."""

    def __init__(self, module: ModuleType, trees: dict[str, Any], size: int) -> None:
        """Prepare source binding and fixture allocation outside timing."""
        self.module = module
        self.created: list[Any] = []
        self.stream = self.generator(timing.SEED, backend="stdlib")
        self.namespaces = {}
        for name, code in trees.items():
            namespace = {
                "TYPE_CHECKING": False,
                "rand": SimpleNamespace(Generator=self.generator),
                "hashlib": hashlib,
                "TrainExample": _Example,
                "_MASKABLE": (_Kind.TOOL_CALL, _Kind.TOOL_CHAIN),
            }
            exec(code, namespace)  # noqa: S102 — checked local captured consumer source
            self.namespaces[name] = SimpleNamespace(**namespace)
        self.schema = _schema()
        self.examples = [
            _Example(
                (_Kind.OTHER if index % 7 == 0 else _Kind.TOOL_CALL),
                f"tool_{index % 64}",
                [
                    {"role": "user", "content": f"request_{index}"},
                    {
                        "role": "assistant",
                        "tool_calls": [{"name": f"tool_{index % 64}"}],
                    },
                ],
            )
            for index in range(size)
        ]
        self.by_tool = {
            f"tool_{group}": self.examples[group * 32 : (group + 1) * 32]
            for group in range(max(1, size // 32))
        }
        self.curiosity = self.namespaces["curiosity"]._Rng()
        self.curiosity.seed(timing.SEED)
        self.population = [f"area_{index}" for index in range(32)]
        self.weights = [float(index + 1) for index in range(32)]
        control = random.Random(timing.SEED)
        self.selected = {
            index
            for index, example in enumerate(self.examples)
            if example.kind in (_Kind.TOOL_CALL, _Kind.TOOL_CHAIN)
            and control.random() < 0.3
        }
        self.size = size

    def generator(self, *args: Any, **kwargs: Any) -> Any:
        """Track internal RNG construction so full consumer states are checked."""
        engine = self.module.Generator(*args, **kwargs)
        self.created.append(engine)
        return engine

    def profile_state(self) -> Any:
        """All internal consumer streams, including streams constructed per call."""
        return (
            tuple(timing._state(engine) for engine in self.created),
            tuple(example.mask_seed for example in self.examples),
        )


def _schema() -> dict[str, Any]:
    """Synthetic nested schema covering exact branches from the real consumer."""
    return {
        "type": "object",
        "required": ["count", "priority", "payload", "active"],
        "$defs": {"priority": {"type": "string", "enum": ["low", "medium", "high"]}},
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 100},
            "priority": {"$ref": "#/$defs/priority"},
            "payload": {
                "type": "object",
                "required": ["value"],
                "properties": {
                    "value": {"type": "number", "minimum": -1.5, "maximum": 4.5},
                    "label": {"type": "string", "minLength": 12, "maxLength": 20},
                },
            },
            "active": {"type": "boolean"},
            "tags": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "mail": {"type": "string", "format": "email"},
            "mode": {"type": ["null", "string"], "default": "auto"},
        },
    }


def _schema_batch(context: Context) -> Any:
    sampler = context.namespaces["schema"].sample_valid_args
    return [sampler(context.schema, context.stream) for _ in range(context.size)]


def _trim(context: Context) -> Any:
    return context.namespaces["corpus_trim"]._trim(
        context.examples, context.size // 2, context.stream
    )


def _split(context: Context) -> Any:
    return context.namespaces["stratified_split"]._split(
        context.by_tool, 0.2, context.stream
    )


def _masking(context: Context) -> Any:
    return context.namespaces["function_masking"].apply_function_masking(
        context.examples, fraction=0.3, seed=timing.SEED
    )


def _masking_control(context: Context) -> Any:
    """The same filtering and hash writes with selection decisions precomputed."""
    namespace = context.namespaces["function_masking"]
    marked = 0
    for index, example in enumerate(context.examples):
        if example.kind not in (
            _Kind.TOOL_CALL,
            _Kind.TOOL_CHAIN,
        ) or not namespace._has_tool_call(example):
            continue
        if index in context.selected:
            example.mask_seed = namespace._mask_seed(timing.SEED, example)
            marked += 1
    return marked


def _curiosity(context: Context) -> Any:
    return context.curiosity.choices(
        context.population, weights=context.weights, k=context.size
    )


def _cases(modules: dict[str, ModuleType], trees: dict[str, Any]) -> list[timing.Case]:
    """Exact consumers at public small and batch input sizes."""
    cases = []
    operations = (
        ("schema", _schema_batch, (16, 256)),
        ("trim", _trim, (256, 8192)),
        ("split", _split, (256, 8192)),
        ("function-masking", _masking, (256, 8192)),
        ("function-masking-selection-precomputed", _masking_control, (256, 8192)),
        ("curiosity-weighted", _curiosity, (1, 16, 256)),
    )
    for name, operation, sizes in operations:
        for size in sizes:
            factories: dict[str, Callable[[], Any]] = {
                label: partial(Context, module, trees, size)
                for label, module in modules.items()
            }
            cases.append(
                timing.Case(
                    f"billy-{name}-{size}",
                    "stdlib (explicit in original consumer)",
                    factories,
                    operation,
                )
            )
    return cases


def _profile(case: timing.Case) -> dict[str, Any]:
    """Separate call profiles bound the rand share without affecting timings."""
    profiles = {}
    for label, factory in case.factories.items():
        context = factory()
        profiler = cProfile.Profile()
        profiler.runcall(case.operation, context)
        stats: Any = pstats.Stats(profiler)
        draw_path = str(Path(context.module.__file__).resolve())
        native = getattr(context.module, "_native", None)
        binding = getattr(context.module, "_native_shuffle", None)
        native_name = (
            native.__name__
            if native is not None
            else (binding.__module__ if binding is not None else None)
        )
        rows = []
        rng_self = 0.0
        for (filename, line, name), (
            primitive,
            calls,
            self_s,
            cumulative_s,
            _callers,
        ) in stats.stats.items():
            is_rand = (
                filename == draw_path
                or filename == random.__file__
                or (
                    filename == "~"
                    and (
                        "_random.Random" in name
                        or (native_name is not None and native_name in name)
                    )
                )
            )
            if is_rand:
                rng_self += self_s
            rows.append(
                {
                    "file": filename,
                    "line": line,
                    "name": name,
                    "calls": calls,
                    "primitive_calls": primitive,
                    "self_s": self_s,
                    "cumulative_s": cumulative_s,
                    "rng_function": is_rand,
                }
            )
        fraction = min(1.0, rng_self / stats.total_tt) if stats.total_tt else 0.0
        profiles[label] = {
            "total_profile_s": stats.total_tt,
            "rng_self_s": rng_self,
            "rng_self_fraction": fraction,
            "ideal_speedup_if_rng_free": 1 / (1 - fraction) if fraction < 1 else None,
            "interpretation": "Profile instrumentation changes costs; this Amdahl estimate is diagnostic, not a timing claim. It charges only exclusive time in draw.py, stdlib random.py, _random.Random primitives and the selected native rand module to RNG.",
            "functions": sorted(rows, key=lambda row: row["self_s"], reverse=True),
        }
    return profiles


def _positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        message = "must be a positive finite number"
        raise argparse.ArgumentTypeError(message)
    return number


def _target_summary(
    workloads: list[dict[str, Any]], target: float | None
) -> dict[str, Any]:
    """Keep a failed requested target visible in every eligible real consumer."""
    ratios = []
    for row in workloads:
        eligible = "selection-precomputed" not in row["name"]
        ratio = row["median_matched_before_over_current"]["wall_ns"]
        row["speedup_target"] = {
            "eligible_real_consumer": eligible,
            "required_before_over_current": target,
            "passed": ratio >= target if target is not None and eligible else None,
        }
        if eligible:
            ratios.append(ratio)
    return {
        "required_before_over_current": target,
        "eligible_real_consumers": len(ratios),
        "geometric_mean_before_over_current": math.exp(
            sum(math.log(ratio) for ratio in ratios) / len(ratios)
        ),
        "minimum_before_over_current": min(ratios),
        "all_real_consumers_passed": all(ratio >= target for ratio in ratios)
        if target is not None
        else None,
        "controls_excluded": "precomputed masking-selection controls",
    }


def main(argv: list[str] | None = None) -> None:
    """Save paired timings, exact consumer receipts and separate RNG profiles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "workloads/billy_rand.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=timing._positive, default=7)
    parser.add_argument("--batch-ms", type=timing._positive, default=5)
    parser.add_argument("--include", action="append", help="repeatable workload glob")
    parser.add_argument(
        "--min-speedup",
        type=_positive_float,
        help="required before/current wall ratio for every real consumer; failed reports are saved",
    )
    parser.add_argument(
        "--require-native",
        action="store_true",
        help="require current source to bind its optional native shuffle kernel",
    )
    args = parser.parse_args(argv)
    if args.rounds < 3:
        parser.error("paired comparisons require at least three rounds")
    modules, evidence = {}, {}
    for label, source in {
        "current": args.source,
        "before": args.compare_source,
    }.items():
        modules[label], evidence[label] = timing._load_source(source)
    timing._check_sources(evidence)
    if args.require_native and not evidence["current"]["native"]:
        parser.error("current source did not bind a native rand kernel")
    trees, receipts = _load_consumers(args.fixtures)
    cases = _cases(modules, trees)
    if args.include:
        cases = [
            case
            for case in cases
            if any(fnmatchcase(case.name, pattern) for pattern in args.include)
        ]
    if not cases:
        parser.error("workload selection matched no cases")
    workloads = []
    for case in cases:
        row = timing._measure(
            case, rounds=args.rounds, minimum_ns=args.batch_ms * 1_000_000
        )
        row["profiles"] = _profile(case)
        workloads.append(row)
    timing._check_sources(evidence)
    if hashlib.sha256(args.fixtures.read_bytes()).hexdigest() != receipts["sha256"]:
        message = "Billy fixture changed during measurement"
        raise RuntimeError(message)
    target = _target_summary(workloads, args.min_speedup)
    report = {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "numpy": getattr(sys.modules.get("numpy"), "__version__", None),
        },
        "sources": evidence,
        "consumer_receipts": receipts,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_harness_sha256": hashlib.sha256(
            Path(timing.__file__).read_bytes()
        ).hexdigest(),
        "scope": "Exact synchronous Billy consumer function slices over labelled synthetic fixture inputs; fixture setup and profiles excluded. Example models project only attributes read by captured functions. This is not full app/network/LLM/database latency.",
        "rounds": args.rounds,
        "method": "adjacent alternating wall/thread CPU batches; independent equally seeded engines and consumer inputs; exact outputs and complete internal RNG states; separate cProfile RNG fraction and precomputed masking-selection control",
        "workloads": workloads,
        "speedup_target": target,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in workloads:
        ratio = row["median_matched_before_over_current"]["wall_ns"]
        current = row["median_per_call_ns"]["current"]["wall_ns"]
        fraction = row["profiles"]["current"]["rng_self_fraction"]
        print(
            f"{row['name']}: {current / 1e6:.3f} ms, {ratio:.3f}x, profiled RNG share {fraction:.1%}"
        )

    if target["all_real_consumers_passed"] is False:
        print(
            f"Requested {args.min_speedup:g}x target failed; complete report saved to {args.output}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
