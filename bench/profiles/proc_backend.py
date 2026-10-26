"""Paired process-runner timings with isolated sources and exact I/O checks.

Run this file directly. Each worker selects its proc source before importing
the product. Baseline and candidate worker order alternates by round; imports,
warmups, and output validation stay outside each timed batch.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import dataclass
from datetime import UTC, datetime
import gc
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Workload:
    """A real child program with independently specified expected output."""

    name: str
    argv: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    stdin_text: str | None = None
    iterations: int = 3
    api: str = "run"
    cwd: str | None = None
    env: dict[str, str] | None = None


def _workloads() -> list[Workload]:
    """Portable shell-free programs, including pipe and process-exit tails."""
    python = (sys.executable, "-S", "-c")
    cases = [
        Workload(
            "short-output",
            (*python, "import sys; print('output'); print('detail', file=sys.stderr)"),
            "output\n",
            "detail\n",
            iterations=12,
        ),
        Workload(
            "closed-pipes-before-exit",
            (*python, "import os,time; os.close(1); os.close(2); time.sleep(0.002)"),
            iterations=12,
        ),
        Workload(
            "closed-pipes-20ms",
            (*python, "import os,time; os.close(1); os.close(2); time.sleep(0.020)"),
            iterations=6,
        ),
        Workload(
            "cwd-env-stdin",
            (
                *python,
                "import os,sys; print(os.getcwd()); print(os.environ['PROC_FIXTURE']); "
                "sys.stdout.write(sys.stdin.read())",
            ),
            f"{_ROOT}\nliteral $HOME café\ninput ☃\n",
            stdin_text="input ☃\n",
            iterations=6,
            cwd=str(_ROOT),
            env={
                "PROC_FIXTURE": "literal $HOME café",
                "PYTHONUTF8": "1",
                "PATH": os.defpath,
            },
        ),
        Workload(
            "capture-short-output",
            (*python, "print('  output  ')"),
            "  output  \n",
            iterations=6,
            api="capture",
        ),
    ]
    executable = shutil.which("true")
    if executable is not None:
        cases.insert(0, Workload("empty-executable", (executable,), iterations=32))
        cases.insert(
            1,
            Workload("run-ok-empty", (executable,), iterations=32, api="run_ok"),
        )
    cases.extend(
        Workload(
            f"stdout-{size // (1024 * 1024)}mib",
            (*python, f"import sys; sys.stdout.buffer.write(b'O' * {size})"),
            "O" * size,
        )
        for size in (1024 * 1024, 16 * 1024 * 1024)
    )
    cases.append(
        Workload(
            "stderr-1mib",
            (*python, "import sys; sys.stderr.buffer.write(b'E' * 1048576)"),
            stderr="E" * (1024 * 1024),
        )
    )
    cases.append(
        Workload(
            "duplex-1mib",
            (
                *python,
                "import sys,threading; "
                "a=threading.Thread(target=sys.stdout.buffer.write,args=(b'O'*1048576,)); "
                "b=threading.Thread(target=sys.stderr.buffer.write,args=(b'E'*1048576,)); "
                "a.start(); b.start(); "
                "assert sys.stdin.buffer.read()==b'I'*1048593; a.join(); b.join()",
            ),
            "O" * (1024 * 1024),
            "E" * (1024 * 1024),
            "I" * (1024 * 1024 + 17),
        )
    )
    return cases


def _check(result: Any, case: Workload) -> None:
    """Check actual complete output rather than length or checksum alone."""
    if case.api == "capture" and type(result) is str and result == case.stdout.strip():
        return
    if case.api == "run_ok" and result is True:
        return
    if case.api != "run":
        message = f"convenience result disagrees with independent fixture: {case.name}"
        raise RuntimeError(message)
    if (
        result.argv != case.argv
        or result.returncode != 0
        or type(result.stdout) is not str
        or type(result.stderr) is not str
        or result.stdout != case.stdout
        or result.stderr != case.stderr
    ):
        message = f"process result disagrees with independent fixture: {case.name}"
        raise RuntimeError(message)


def _measure(run: Callable[..., Any], case: Workload) -> dict[str, Any]:
    """Time complete calls, retaining outputs for validation after the timer."""
    options: dict[str, Any] = {"stdin_text": case.stdin_text, "timeout": 10}
    if case.cwd is not None:
        options["cwd"] = case.cwd
    if case.env is not None:
        options["env"] = case.env
    profiler = cProfile.Profile()
    result = profiler.runcall(run, case.argv, **options)
    _check(result, case)
    route = {}
    for entry in profiler.getstats():
        code = entry.code
        location = (
            code
            if isinstance(code, str)
            else f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
        )
        if any(
            part in location
            for part in ("sleep", "pidfd", "kqueue", "poll", "waitpid", ":_wait")
        ):
            route[location] = entry.callcount
    enabled = gc.isenabled()
    gc.disable()
    try:
        cpu_start = time.process_time_ns()
        wall_start = time.perf_counter_ns()
        results = [run(case.argv, **options) for _ in range(case.iterations)]
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.process_time_ns() - cpu_start
    finally:
        if enabled:
            gc.enable()
    for result in results:
        _check(result, case)
    return {
        "iterations": case.iterations,
        "wall_ns": wall_ns,
        "parent_cpu_ns": cpu_ns,
        "wall_ns_per_call": wall_ns / case.iterations,
        "parent_cpu_ns_per_call": cpu_ns / case.iterations,
        "output_bytes_per_call": len(case.stdout) + len(case.stderr),
        "untimed_wait_profile_calls": route,
    }


def _source_hashes(source: Path) -> dict[str, str]:
    """Fingerprint every Python file in the selected proc implementation."""
    directory = source / "unstd" / "proc"
    if not (directory / "__init__.py").is_file():
        message = f"source must contain unstd/proc/__init__.py: {source}"
        raise ValueError(message)
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*.py"))
    }


def _worker(source: Path, selected: list[str]) -> dict[str, Any]:
    """Import only the requested proc source in a fresh worker process."""
    source = source.resolve()
    hashes = _source_hashes(source)
    if any(name == "unstd" or name.startswith("unstd.") for name in sys.modules):
        message = "worker imported product code before source selection"
        raise RuntimeError(message)
    # A namespace root selects the saved proc subtree without executing another
    # installation's top-level package or requiring a complete product snapshot.
    root = ModuleType("unstd")
    root.__path__ = [str(source / "unstd")]
    sys.modules["unstd"] = root
    proc = importlib.import_module("unstd.proc")
    filename = proc.__file__
    if filename is None or Path(filename).resolve() != (
        source / "unstd" / "proc" / "__init__.py"
    ):
        message = "requested proc source was not imported"
        raise RuntimeError(message)
    cases = _workloads()
    known = {case.name for case in cases}
    if set(selected) - known:
        message = f"unknown workloads: {sorted(set(selected) - known)}"
        raise ValueError(message)
    measurements = {
        case.name: _measure(getattr(proc, case.api), case)
        for case in cases
        if not selected or case.name in selected
    }
    if hashes != _source_hashes(source):
        message = "proc source changed during measurement"
        raise RuntimeError(message)
    return {
        "source": str(source),
        "source_hashes": hashes,
        "imported_source": str(Path(filename).resolve()),
        "signatures": {
            name: str(inspect.signature(getattr(proc, name)))
            for name in ("run", "run_ok", "capture")
        },
        "measurements": measurements,
    }


def _compare_signatures(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Refuse speed ratios between changed public call contracts."""
    if baseline["signatures"] != candidate["signatures"]:
        message = "public proc signatures changed between compared sources"
        raise RuntimeError(message)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Alternate fresh baseline/current workers and retain all raw samples."""
    sources = {
        "baseline": args.baseline_source.resolve(),
        "candidate": args.source.resolve(),
    }
    initial_hashes = {name: _source_hashes(path) for name, path in sources.items()}
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in sources}
    for index in range(args.rounds):
        order: tuple[str, ...] = ("baseline", "candidate")
        if index % 2:
            order = tuple(reversed(order))
        for name in order:
            command = [
                sys.executable,
                "-S",
                str(Path(__file__).resolve()),
                "--worker",
                "--source",
                str(sources[name]),
            ]
            for case in args.case:
                command.extend(("--case", case))
            worker = subprocess.run(  # noqa: S603 — fixed local script, explicit argv
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=True,
            )
            sample = json.loads(worker.stdout)
            if sample["source_hashes"] != initial_hashes[name]:
                message = f"{name} source changed across paired rounds"
                raise RuntimeError(message)
            samples[name].append(sample)
        _compare_signatures(samples["baseline"][-1], samples["candidate"][-1])
    summary = {}
    for case in samples["baseline"][0]["measurements"]:
        baseline = [
            sample["measurements"][case]["wall_ns_per_call"]
            for sample in samples["baseline"]
        ]
        candidate = [
            sample["measurements"][case]["wall_ns_per_call"]
            for sample in samples["candidate"]
        ]
        summary[case] = {
            "baseline_median_wall_ns": statistics.median(baseline),
            "candidate_median_wall_ns": statistics.median(candidate),
            "median_wall_speedup": statistics.median(baseline)
            / statistics.median(candidate),
            "paired_wall_speedups": [
                before / after
                for before, after in zip(baseline, candidate, strict=True)
            ],
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "measurement": "complete proc.run wall time; parent CPU excludes child CPU",
        "rounds": args.rounds,
        "summary": summary,
        "samples": samples,
    }


def main() -> None:
    """Write paired evidence, or emit one source-isolated worker result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(_worker(args.source, args.case)))
        return
    if args.baseline_source is None or args.output is None or args.rounds < 1:
        parser.error("--baseline-source, --output, and positive --rounds are required")
    result = _run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for name, row in result["summary"].items():
        print(
            f"{name}: {row['baseline_median_wall_ns'] / 1e6:.3f} -> "
            f"{row['candidate_median_wall_ns'] / 1e6:.3f} ms "
            f"({row['median_wall_speedup']:.3f}x)"
        )


if __name__ == "__main__":
    main()
