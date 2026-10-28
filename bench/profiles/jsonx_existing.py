"""Compare three JSONX sources using the existing public benchmark fixtures.

Run this file directly so a worker can select its product and harness before
either is imported::

    python bench/profiles/jsonx_existing.py \
        --original /saved/original/src --first-pass /saved/first-pass/src \
        --candidate /saved/candidate/src --harness /saved/harness \
        --output /results/jsonx-existing.json

Each source contains ``unstd`` and its built native extension. Its sibling
``native-serde`` or ``native/serde`` directory contains the matching C sources.
The harness contains ``bench`` with its unchanged fixtures and baselines.
``--smoke`` checks imports and all existing oracles without timing or profiling.

Workers use the same interpreter and dependencies. Source positions rotate
across repetitions; three repetitions balance all positions. Public Python
calls include their guards and allocations. Profiling is a separate pass and
never contributes to throughput ratios. No native component is timed directly.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, distribution, version
import json
import os
from pathlib import Path
import platform
import pstats
from statistics import median
import subprocess
import sys
import sysconfig
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Sequence

    from bench.core.cases import Case as CoreCase
    from bench.profiles.serde_profile import Workload
    from bench.speeds import Case


_LABELS = ("original", "first_pass", "candidate")
# Keep this historical comparison on APIs shared by its saved products. New
# registry entries (such as array streaming) have their own workload evidence.
_PUBLIC_CASE_NAMES = frozenset(
    f"unstd.serde.jsonx.{name}"
    for name in (
        "dumps",
        "dumpb",
        "loads",
        "canonical",
        "canonical_strict",
        "dump",
        "load",
        "JSONEncoder",
        "JSONDecoder",
    )
)
_FIXTURE_FILES = (
    "bench/workloads/speed_cases_codecs.py",
    "bench/core/cases.py",
    "bench/core/timing.py",
    "bench/core/report.py",
    "bench/speeds.py",
    "bench/profiles/serde_profile.py",
)


def _backend_fingerprint(name: str) -> dict[str, Any] | None:
    try:
        installed = distribution(name)
    except PackageNotFoundError:
        return None
    hashes = {
        str(path): sha256(
            Path(str(installed.locate_file(path))).read_bytes()
        ).hexdigest()
        for path in sorted(installed.files or (), key=str)
        if path.suffix in {".py", ".so", ".pyd", ".dylib"}
    }
    return {
        "version": installed.version,
        "code_files": len(hashes),
        "code_sha256": sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "native_sha256": {
            name: digest
            for name, digest in hashes.items()
            if Path(name).suffix in {".so", ".pyd", ".dylib"}
        },
    }


def _environment() -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    library = Path(sysconfig.get_config_var("LIBDIR") or "") / (
        sysconfig.get_config_var("LDLIBRARY") or ""
    )
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "backends": {name: version(name) for name in ("orjson", "msgspec", "pybase64")},
        "runtime_fingerprint": {
            "executable": str(executable),
            "executable_sha256": sha256(executable.read_bytes()).hexdigest(),
            "library": str(library.resolve()) if library.is_file() else None,
            "library_sha256": sha256(library.read_bytes()).hexdigest()
            if library.is_file()
            else None,
            "compiler_configuration": {
                name: sysconfig.get_config_var(name)
                for name in ("CC", "CFLAGS", "PY_CFLAGS", "CONFIG_ARGS", "SOABI")
            },
        },
        "backend_fingerprints": {
            name: _backend_fingerprint(name)
            for name in ("orjson", "msgspec", "pybase64", "numpy", "blake3")
        },
        "thread_environment": {
            name: os.environ.get(name)
            for name in (
                "PYTHONHASHSEED",
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
    }


def _provenance(source: Path, jsonx: Any) -> dict[str, Any]:
    imported = Path(jsonx.__file__).resolve()
    if imported != source / "unstd/serde/jsonx.py":
        message = f"requested {source}, imported {imported}"
        raise RuntimeError(message)
    package = imported.parent
    native = getattr(jsonx, "_native", None)
    binary = Path(native.__file__).resolve() if native is not None else None
    if binary is None or binary.parent != package:
        message = f"requested native extension in {package}, imported {binary}"
        raise RuntimeError(message)
    native_source = source.parent / "native-serde"
    if not native_source.is_dir():
        native_source = source.parent / "native/serde"
    native_hashes = {
        str(path.relative_to(native_source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(native_source.rglob("*"))
        if path.is_file() and path.suffix in {".c", ".h"}
    }
    if not native_hashes:
        message = f"matching native C/header sources are missing beside {source}"
        raise RuntimeError(message)
    return {
        "source": str(source),
        "jsonx": str(imported),
        "native_binary": str(binary),
        "python_sha256": {
            str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(package.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
        },
        "native_source_root": str(native_source),
        "native_source_sha256": native_hashes,
    }


def _harness_hashes(harness: Path) -> dict[str, str]:
    for name in _FIXTURE_FILES:
        if not (harness / name).is_file():
            message = f"missing existing harness file: {harness / name}"
            raise RuntimeError(message)
    files = [*(harness / "bench").rglob("*.py")]
    files.extend((harness / "bench/baselines").glob("*.json"))
    return {
        str(path.relative_to(harness)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
        if path.is_file()
    }


def _check_harness(harness: Path, functions: Sequence[Any]) -> None:
    for function in functions:
        path = Path(function.__code__.co_filename).resolve()
        if not path.is_relative_to(harness / "bench"):
            message = f"requested harness {harness}, imported {path}"
            raise RuntimeError(message)


def _audit_existing(
    public_cases: Sequence[Case],
    core_cases: Sequence[CoreCase],
    profile_cases: Sequence[Workload],
) -> None:
    from bench.core.cases import audit as core_audit
    from bench.profiles.serde_profile import _same
    from bench.speeds import audit

    audit(public_cases)
    unavailable = [case.name for case in core_cases if not case.available]
    if unavailable:
        message = f"missing existing core benchmark backends: {unavailable}"
        raise RuntimeError(message)
    # Unlike the speed registry's audit, the core audit returns divergences.
    if divergences := core_audit(core_cases):
        message = f"existing core correctness audit failed: {divergences}"
        raise AssertionError(message)
    for case in profile_cases:
        if not _same(case.call(), case.expected):
            message = f"existing profile correctness audit failed: {case.name}"
            raise AssertionError(message)


def _public_profile(cases: Sequence[Case], source: Path) -> list[dict[str, Any]]:
    # canonical_strict is present in the registry but absent from the older
    # serde profile. Exercise that unchanged callable in a separate profile.
    profile = cProfile.Profile()
    profile.enable()
    for _ in range(10):
        for case in cases:
            case.run()
    profile.disable()
    profile_stats: Any = pstats.Stats(profile)
    stats = profile_stats.stats
    functions = []
    for (filename, line, name), entry in sorted(stats.items()):
        path = Path(filename)
        if not path.is_relative_to(source / "unstd/serde"):
            continue
        functions.append(
            {
                "file": str(path.relative_to(source)),
                "line": line,
                "function": name,
                "primitive_calls": entry[0],
                "total_calls": entry[1],
                "self_ns": entry[2] * 1e9,
                "cumulative_ns": entry[3] * 1e9,
            }
        )
    return functions


def _worker(
    source: Path, rounds: int, profile_rounds: int, *, smoke: bool, harness: Path
) -> dict[str, Any]:
    from bench.core.cases import registry
    from bench.core.report import run
    from bench.core.timing import measure
    from bench.profiles.serde_profile import _worker as profile_worker
    from bench.profiles.serde_profile import _workloads
    from bench.speeds import timings
    from bench.workloads.speed_cases_codecs import _serde_cases
    from unstd.serde import jsonx

    _check_harness(
        harness,
        (registry, run, measure, timings, profile_worker, _workloads, _serde_cases),
    )
    metadata = _provenance(source, jsonx)
    harness_hashes = _harness_hashes(harness)
    public_cases = [case for case in _serde_cases() if case.name in _PUBLIC_CASE_NAMES]
    core_cases = [
        case for case in registry(("serde",)) if case.name.startswith("jsonx.")
    ]
    if (
        {case.name for case in public_cases} != _PUBLIC_CASE_NAMES
        or len(public_cases) != len(_PUBLIC_CASE_NAMES)
        or len(core_cases) != 7
    ):
        message = "expected the unchanged 9 public registry and 7 core JSONX cases"
        raise RuntimeError(message)
    profile_cases = _workloads()
    _audit_existing(public_cases, core_cases, profile_cases)
    result: dict[str, Any] = {
        "environment": _environment(),
        "provenance": metadata,
        "harness_sha256": harness_hashes,
        "public_case_names": [case.name for case in public_cases],
        "core_case_names": [case.name for case in core_cases],
        "existing_profile_case_names": [case.name for case in profile_cases],
    }
    if not smoke:
        public_timings = timings(public_cases, rounds=rounds)
        core_results = run(core_cases, rounds=rounds)
        # The unchanged worker times first, then profiles in a separate pass.
        profile = profile_worker(source, profile_rounds, False)
        result.update(
            public_registry={
                case.name: {
                    "note": case.note,
                    "measurement": asdict(public_timings[case.name]),
                }
                for case in public_cases
            },
            core_registry={
                row.case.name: {
                    "note": row.case.note,
                    "accel": asdict(row.accel) if row.accel else None,
                    "stdlib": asdict(row.base) if row.base else None,
                    "floor": row.floor,
                    "floor_failed": row.failed,
                    "skipped": row.skipped,
                }
                for row in core_results
            },
            existing_serde_profile=profile,
            existing_public_registry_python_profile=_public_profile(
                public_cases, source
            ),
        )
        _audit_existing(public_cases, core_cases, profile_cases)
    if _provenance(source, jsonx) != metadata:
        message = "product source or native binary changed during the worker"
        raise RuntimeError(message)
    if _harness_hashes(harness) != harness_hashes:
        message = "benchmark harness changed during the worker"
        raise RuntimeError(message)
    if _environment() != result["environment"]:
        message = "interpreter or backend installation changed during the worker"
        raise RuntimeError(message)
    return result


def _comparisons(runs: Sequence[dict[str, Any]], group: str) -> dict[str, Any]:
    key = "measurement" if group == "public_registry" else "accel"
    result = {}
    for name in sorted(runs[0][group]):
        latencies = {
            label: median(
                run[group][name][key]["per_op_ns"]
                for run in runs
                if run["label"] == label
            )
            for label in _LABELS
        }
        result[name] = _ratios(latencies)
    return result


def _ratios(latencies: dict[str, float]) -> dict[str, Any]:
    return {
        "ns": latencies,
        "final_over_original_speedup": latencies["original"] / latencies["candidate"],
        "final_over_first_pass_speedup": latencies["first_pass"]
        / latencies["candidate"],
    }


def _validate_runs(runs: Sequence[dict[str, Any]], *, smoke: bool) -> None:
    first = runs[0]
    for run in runs:
        for name in (
            "environment",
            "harness_sha256",
            "public_case_names",
            "core_case_names",
            "existing_profile_case_names",
        ):
            if run[name] != first[name]:
                message = f"comparison workers differ: {name}"
                raise RuntimeError(message)
        if not smoke and (
            run["existing_serde_profile"]["signatures"]
            != first["existing_serde_profile"]["signatures"]
        ):
            message = "comparison workers expose different public signatures"
            raise RuntimeError(message)
        prior = next(row for row in runs if row["label"] == run["label"])
        if prior["provenance"] != run["provenance"]:
            message = f"{run['label']} source changed between repetitions"
            raise RuntimeError(message)


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        message = "must be a positive integer"
        raise argparse.ArgumentTypeError(message)
    return number


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path)
    parser.add_argument("--first-pass", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--rounds", type=_positive, default=5)
    parser.add_argument("--profile-rounds", type=_positive, default=3)
    parser.add_argument("--repetitions", type=_positive, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    required = (
        ("source",)
        if args.worker
        else (
            "original",
            "first_pass",
            "candidate",
            "output",
        )
    )
    for name in required:
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    if not args.smoke and args.rounds < 3:
        parser.error("--rounds must be at least three for the existing speed timer")
    return args


def _save(output: Path, report: dict[str, Any]) -> None:
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    harness = args.harness.resolve()
    driver = Path(__file__).resolve()
    sources = (
        ("original", args.original.resolve()),
        ("first_pass", args.first_pass.resolve()),
        ("candidate", args.candidate.resolve()),
    )
    harness_hashes = _harness_hashes(harness)
    runs: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "purpose": "Three-source comparison of existing public Python JSONX fixtures and separate Python profiles",
        "smoke": args.smoke,
        "driver_sha256": sha256(driver.read_bytes()).hexdigest(),
        "driver_source": driver.read_text(encoding="utf-8"),
        "driver_command": [sys.executable, *sys.argv],
        "harness_root": str(harness),
        "harness_sha256": harness_hashes,
        "fixture_sha256": {name: harness_hashes[name] for name in _FIXTURE_FILES},
        "estimator": "Median across cyclically rotated source repetitions of minimum calibrated wall-clock batch means. Three repetitions balance source positions. Profiler times are excluded from throughput ratios. Existing serde_profile retains its original summary statistics; registries retain raw wall and thread-CPU samples.",
        "configuration": {
            "rounds": args.rounds,
            "profile_rounds": args.profile_rounds,
            "repetitions": args.repetitions,
        },
        "existing_entrypoints": {
            "public_registry": "bench.speeds.timings(JSONX _serde_cases(), rounds)",
            "core_registry": "bench.core.report.run(JSONX registry(('serde',)), rounds)",
            "serde_profile": "bench.profiles.serde_profile._worker(source, profile_rounds, False)",
            "registry_python_profile": "cProfile over ten passes of the unchanged public registry callables, after all timings",
        },
        "runs": runs,
    }
    started = time.monotonic()
    for repetition in range(args.repetitions):
        offset = repetition % len(sources)
        for label, source in sources[offset:] + sources[:offset]:
            command = [
                sys.executable,
                str(driver),
                "--worker",
                "--source",
                str(source),
                "--rounds",
                str(args.rounds),
                "--profile-rounds",
                str(args.profile_rounds),
                "--harness",
                str(harness),
            ]
            if args.smoke:
                command.append("--smoke")
            environment = dict(
                os.environ,
                PYTHONPATH=os.pathsep.join((str(source), str(harness))),
                PYTHONDONTWRITEBYTECODE="1",
            )
            completed = subprocess.run(  # noqa: S603 — fixed driver and interpreter.
                command,
                cwd=harness,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode:
                report["failure"] = {
                    "label": label,
                    "repetition": repetition + 1,
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
                _save(args.output, report)
                message = f"{label} worker failed; evidence retained in {args.output}"
                raise RuntimeError(message)
            result = json.loads(completed.stdout)
            runs.append(
                {
                    "label": label,
                    "repetition": repetition + 1,
                    "worker_command": command,
                    **result,
                }
            )
            _save(args.output, report)
            if result["harness_sha256"] != harness_hashes:
                message = "worker benchmark harness differs from the saved harness"
                raise RuntimeError(message)
            print(
                f"{sys.version.split()[0]}: {label} repetition {repetition + 1} "
                f"({time.monotonic() - started:.1f}s)",
                file=sys.stderr,
            )
    _validate_runs(runs, smoke=args.smoke)
    if not args.smoke:
        report["public_registry_comparisons"] = _comparisons(runs, "public_registry")
        report["core_registry_comparisons"] = _comparisons(runs, "core_registry")
        report["existing_serde_profile_comparisons"] = {
            name: _ratios(
                {
                    label: median(
                        run["existing_serde_profile"]["timings"][name]["ns"]
                        for run in runs
                        if run["label"] == label
                    )
                    for label in _LABELS
                }
            )
            for name in sorted(runs[0]["existing_serde_profile"]["timings"])
            if name.startswith("jsonx.")
        }
    report["complete"] = True
    _save(args.output, report)
    return report


def main(argv: Sequence[str] | None = None) -> None:
    """Run isolated comparisons, or one internal worker, with explicit paths."""
    args = _arguments(argv)
    if args.worker:
        result = _worker(
            args.source.resolve(),
            args.rounds,
            args.profile_rounds,
            smoke=args.smoke,
            harness=args.harness.resolve(),
        )
        print(json.dumps(result))
    else:
        _compare(args)


if __name__ == "__main__":
    main()
