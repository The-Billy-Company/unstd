"""Compare complete synthetic writers through stdlib and two filesystem APIs."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable
import cProfile
from dataclasses import dataclass
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import ModuleType
from typing import TypedDict, cast

from unstd import fs


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
type Writer = Callable[[Path, dict[str, object]], Path | None]


class ProfileRow(TypedDict):
    """Precise call counts and elapsed profiler counters."""

    file: str
    line: int
    function: str
    calls: int
    self_seconds: float
    cumulative_seconds: float


class TimingResult(TypedDict):
    """Paired samples for one complete write shape."""

    method_revision: str
    input_objects: str
    parent_state: str
    rounds: int
    calls_per_round_per_arm: int
    round_medians_ns: dict[str, dict[str, list[float]]]
    raw_call_samples_ns: dict[str, dict[str, list[float]]]
    median_ns: dict[str, dict[str, float]]
    ratios_to_stdlib: dict[str, dict[str, float]]


class ConsumerResult(TypedDict):
    """Behavior, timing and profiles for one writer."""

    function: str
    parity: list[dict[str, object]]
    timing: TimingResult
    profile: dict[str, list[ProfileRow]]


class Arguments(argparse.Namespace):
    """Validated benchmark command arguments."""

    before: Path
    candidate: Path
    out: Path
    rounds: int
    loops: int
    fresh: bool
    control: bool
    shared_path_object: bool


@dataclass(frozen=True)
class Consumer:
    """One pair of equivalent complete writer shapes."""

    name: str
    before: str
    after: str
    value: dict[str, object]


def record_reference(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(f".{os.getpid()}.tmp")
    pending.write_text(json.dumps(value, sort_keys=True) + "\n")
    pending.replace(path)


def record_shared(path: Path, value: dict[str, object]) -> None:
    fs.ensure_dir(path.parent)
    pending = path.with_suffix(f".{os.getpid()}.tmp")
    pending.write_text(json.dumps(value, sort_keys=True) + "\n")
    fs.atomic_replace(pending, path)


def sidecar_for(target: Path) -> Path:
    return target.with_name(target.name + SIDECAR_SUFFIX)


def sidecar_reference(target: Path, value: dict[str, object]) -> Path:
    side = sidecar_for(target)
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return side


def sidecar_shared(target: Path, value: dict[str, object]) -> Path:
    side = sidecar_for(target)
    fs.ensure_dir(side.parent)
    side.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return side


SIDECAR_SUFFIX = ".metadata.json"
CONSUMERS = (
    Consumer(
        "record", "record_reference", "record_shared", {"schema": 1, "unicode": "λ"}
    ),
    Consumer(
        "sidecar", "sidecar_reference", "sidecar_shared", {"title": "λ", "bytes": 123}
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_module(path: Path) -> ModuleType:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    module = ModuleType("filesystem_profile_candidate")
    module.__file__ = str(path)
    # The selected local filesystem source is the explicit benchmark input.
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def load(
    consumer: Consumer,
    *,
    old: bool,
    api: ModuleType,
    force_check_first: bool | None = None,
) -> Writer:
    path = Path(__file__)
    entrypoint = consumer.before if old else consumer.after
    names = {entrypoint, "sidecar_for"}
    tree = ast.parse(path.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if entrypoint not in {function.name for function in functions}:
        message = f"Missing source definitions: {path}"
        raise ValueError(message)
    if not old:
        for function in functions:
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "fs"
                    and node.func.attr == "ensure_dir"
                ):
                    node.keywords = [
                        kw for kw in node.keywords if kw.arg != "check_first"
                    ]
                    if force_check_first is not None:
                        node.keywords.append(
                            ast.keyword(
                                arg="check_first", value=ast.Constant(force_check_first)
                            )
                        )
    namespace: dict[str, object] = {
        "Path": Path,
        "fs": api,
        "json": json,
        "os": os,
        "SIDECAR_SUFFIX": SIDECAR_SUFFIX,
    }
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *functions,
        ],
        type_ignores=[],
    )
    # Fixed local source definitions are compiled to avoid importing application boot code.
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)  # noqa: S102
    loaded = namespace[entrypoint]
    if not callable(loaded):
        message = f"Extracted writer is not callable: {path}:{entrypoint}"
        raise TypeError(message)
    # The narrow dynamic boundary compiles the exact named, two-argument source
    # function above; all calls and returned values remain checked by parity.
    return cast("Writer", loaded)


def snapshot(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        row: dict[str, object] = {"path": str(path.relative_to(root))}
        if path.is_symlink():
            row["link"] = str(path.readlink()).replace(str(root), "$ROOT")
        elif path.is_file():
            row.update(mode=path.stat().st_mode & 0o7777, hex=path.read_bytes().hex())
        else:
            row.update(directory=True, mode=path.stat().st_mode & 0o7777)
        rows.append(row)
    return rows


def parity(consumer: Consumer, functions: dict[str, Writer]) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for shape in (
        "missing",
        "existing",
        "parent-file",
        "linked-parent",
        "linked-destination",
    ):
        observations: list[dict[str, object]] = []
        for function in functions.values():
            with tempfile.TemporaryDirectory(prefix="fs-consumer-parity-") as scratch:
                root = Path(scratch)
                path = root / "nested/record.json"
                target = (
                    path.with_name(path.name + SIDECAR_SUFFIX)
                    if consumer.name == "sidecar"
                    else path
                )
                if shape in ("existing", "linked-destination"):
                    path.parent.mkdir()
                    if shape == "existing":
                        target.write_text("old\n")
                        target.chmod(0o640)
                    else:
                        referent = root / "referent"
                        referent.write_text("old referent\n")
                        target.symlink_to(referent)
                elif shape == "parent-file":
                    path.parent.write_text("blocked")
                elif shape == "linked-parent":
                    referent = root / "linked"
                    referent.mkdir()
                    path.parent.symlink_to(referent, target_is_directory=True)
                status: dict[str, object]
                try:
                    result = function(path, consumer.value)
                    status = {
                        "result": str(result).replace(str(root), "$ROOT")
                        if isinstance(result, Path)
                        else result
                    }
                except OSError as error:
                    status = {
                        "error": type(error).__name__,
                        "errno": error.errno,
                        "filename": str(error.filename).replace(str(root), "$ROOT"),
                    }
                observations.append({"status": status, "tree": snapshot(root)})
        if any(observation != observations[0] for observation in observations[1:]):
            raise AssertionError((consumer.name, shape, observations))
        cases.append({"shape": shape, "all_equal": True})
    return cases


def timing(
    consumer: Consumer,
    functions: dict[str, Writer],
    *,
    rounds: int,
    loops: int,
    fresh: bool,
    independent_path_objects: bool,
) -> TimingResult:
    samples: dict[str, dict[str, list[float]]] = {
        name: {"wall": [], "cpu": []} for name in functions
    }
    raw_samples: dict[str, dict[str, list[float]]] = {
        name: {"wall": [], "cpu": []} for name in functions
    }
    orders = tuple(itertools.permutations(functions))
    with tempfile.TemporaryDirectory(prefix="fs-consumer-timing-") as scratch:
        root = Path(scratch)
        path = root / "nested/record.json" if fresh else root / "record.json"
        paths = dict.fromkeys(functions, path)
        if independent_path_objects:
            for name in functions:
                paths[name] = (
                    root / "nested/record.json" if fresh else root / "record.json"
                )
        published = (
            path.with_name(path.name + SIDECAR_SUFFIX)
            if consumer.name == "sidecar"
            else path
        )
        if not fresh:
            for name, function in functions.items():
                function(paths[name], consumer.value)
        for round_index in range(rounds):
            round_samples: dict[str, dict[str, list[float]]] = {
                name: {"wall": [], "cpu": []} for name in functions
            }
            for index in range(loops):
                for name in orders[(index + round_index) % len(orders)]:
                    path = paths[name]
                    started_wall = time.perf_counter_ns()
                    started_cpu = time.process_time_ns()
                    functions[name](path, consumer.value)
                    cpu = time.process_time_ns() - started_cpu
                    wall = time.perf_counter_ns() - started_wall
                    round_samples[name]["wall"].append(wall)
                    round_samples[name]["cpu"].append(cpu)
                    raw_samples[name]["wall"].append(wall)
                    raw_samples[name]["cpu"].append(cpu)
                    if fresh:
                        published.unlink()
                        path.parent.rmdir()
                        if path.parent.exists():
                            message = "Fresh parent survived cleanup"
                            raise RuntimeError(message)
            for name in functions:
                for clock in ("wall", "cpu"):
                    samples[name][clock].append(
                        statistics.median(round_samples[name][clock])
                    )
    medians = {
        name: {clock: statistics.median(values) for clock, values in clocks.items()}
        for name, clocks in samples.items()
    }
    return {
        "method_revision": "shared-physical-path-v2",
        "input_objects": "independent" if independent_path_objects else "shared",
        "parent_state": "fresh" if fresh else "existing",
        "rounds": rounds,
        "calls_per_round_per_arm": loops,
        "round_medians_ns": samples,
        "raw_call_samples_ns": raw_samples,
        "median_ns": medians,
        "ratios_to_stdlib": {
            name: {
                clock: clocks[clock] / medians["stdlib"][clock]
                for clock in ("wall", "cpu")
            }
            for name, clocks in medians.items()
            if name != "stdlib"
        },
    }


def profile(
    consumer: Consumer, functions: dict[str, Writer]
) -> dict[str, list[ProfileRow]]:
    result: dict[str, list[ProfileRow]] = {}
    with tempfile.TemporaryDirectory(prefix="fs-consumer-profile-") as scratch:
        path = Path(scratch) / "record.json"
        for name, function in functions.items():
            profiler = cProfile.Profile(timer=time.process_time_ns, timeunit=1e-9)
            profiler.enable()
            for _ in range(500):
                function(path, consumer.value)
            profiler.disable()
            rows: list[ProfileRow] = []
            for entry in profiler.getstats():
                code = entry.code
                rows.append(
                    {
                        "file": "~"
                        if isinstance(code, str)
                        else profile_path(code.co_filename),
                        "line": 0 if isinstance(code, str) else code.co_firstlineno,
                        "function": code if isinstance(code, str) else code.co_name,
                        "calls": entry.callcount,
                        "self_seconds": entry.inlinetime,
                        "cumulative_seconds": entry.totaltime,
                    }
                )
            result[name] = sorted(
                rows, key=lambda row: row["cumulative_seconds"], reverse=True
            )
    return result


def profile_path(filename: str) -> str:
    """Keep reusable source identities without retaining local home directories."""
    if filename.startswith("<"):
        return filename
    path = Path(filename).absolute()
    if path.is_relative_to(ROOT):
        return str(path.relative_to(ROOT))
    return f"external/{path.parent.name}/{path.name}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=OUT / "before-fs.py.gz")
    parser.add_argument(
        "--candidate", type=Path, default=ROOT / "src/unstd/fs/__init__.py"
    )
    parser.add_argument("--out", type=Path, default=OUT / "reproduction.json")
    parser.add_argument("--rounds", type=int, default=18)
    parser.add_argument("--loops", type=int, default=300)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--shared-path-object", action="store_true")
    args = parser.parse_args(namespace=Arguments())
    if args.rounds <= 0 or args.loops <= 0:
        parser.error("rounds and loops must be positive")
    candidate = args.candidate.resolve()
    source_hash = digest(candidate)
    api = source_module(candidate)
    before_api = source_module(args.before)
    sources = {
        "harness": digest(Path(__file__)),
        "original_capture_gzip": digest(args.before),
        "candidate_fs_source": source_hash,
    }
    rows: list[ConsumerResult] = []
    for consumer in CONSUMERS:
        functions = {
            "stdlib": load(consumer, old=True, api=before_api),
            "original": load(consumer, old=False, api=before_api),
            "candidate": load(consumer, old=False, api=api),
            "candidate_mkdir_policy": load(
                consumer, old=False, api=api, force_check_first=False
            ),
        }
        if args.control:
            functions = {
                "stdlib": functions["stdlib"],
                "stdlib_control": functions["stdlib"],
            }
        rows.append(
            {
                "function": consumer.name,
                "parity": parity(consumer, functions),
                "timing": timing(
                    consumer,
                    functions,
                    rounds=args.rounds,
                    loops=args.loops,
                    fresh=args.fresh,
                    independent_path_objects=not args.shared_path_object,
                ),
                "profile": profile(consumer, functions),
            }
        )
    if digest(candidate) != source_hash:
        message = "Candidate changed during measurement"
        raise RuntimeError(message)
    evidence = {
        "python": sys.version,
        "profile_clock": "process_cpu",
        "sources_sha256": sources,
        "consumers": rows,
    }
    args.out.write_text(json.dumps(evidence, indent=2) + "\n")
    for row in rows:
        print(
            json.dumps(
                {
                    "function": row["function"],
                    "median_ns": row["timing"]["median_ns"],
                    "ratios_to_stdlib": row["timing"]["ratios_to_stdlib"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
