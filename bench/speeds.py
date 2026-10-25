"""Per-public-function latency registry and frozen regression ceilings.

Run ``python -m bench.speeds check``, ``freeze`` or ``verify``. Absolute
latencies belong to their recorded environment; verification refuses a different
interpreter, CPU, backend version or native-extension availability.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import importlib
from importlib import metadata
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tomllib
from typing import TYPE_CHECKING, Any, NoReturn

from bench.core.timing import Measurement, measure


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "bench/baselines/speeds.json"
_PROVIDERS = ("codecs", "runtime", "operations")
_NATIVE = (
    "clone._clone_native",
    "compress._gzip_native",
    "crypto._stream_native",
    "serde._json_native",
    "text._text_native",
)
_SCHEMA = 1


class RegistryError(ValueError):
    """Incomplete registry, invalid baseline, or incomparable measurement."""


@dataclass(frozen=True, slots=True)
class Case:
    """One bounded operation, with its correctness oracle outside the timer."""

    name: str
    run: Callable[[], object]
    check: Callable[[Any], None]
    note: str = ""

    @property
    def package(self) -> str:
        """The owning public package."""
        return self.name.split(".")[1]


def expect(expected: object) -> Callable[[Any], None]:
    """Build an independent exact-type equality oracle."""

    def check(actual: object) -> None:
        if not _same(actual, expected):
            message = f"expected {expected!r} ({type(expected).__name__}), got {actual!r} ({type(actual).__name__})"
            raise RegistryError(message)

    return check


def _same(actual: object, expected: object) -> bool:
    """Retain nested types and dictionary order in builtin fixture values."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return len(actual) == len(expected) and all(
            _same(key, other) and _same(value, expected[other])
            for (key, value), other in zip(actual.items(), expected, strict=True)
        )
    if isinstance(actual, list | tuple) and isinstance(expected, list | tuple):
        return len(actual) == len(expected) and all(
            _same(value, other) for value, other in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _invalid(message: str) -> NoReturn:
    """Raise a registry diagnostic from a parsing or CLI boundary."""
    raise RegistryError(message)


@contextmanager
def registry(
    groups: Sequence[str] = (), *, comparison: bool = False
) -> Iterator[list[Case]]:
    """Prepare fixtures once and release them even after failed checks/timing."""
    with ExitStack() as stack:
        selected = []
        for provider in _PROVIDERS:
            module = importlib.import_module(f"bench.workloads.speed_cases_{provider}")
            selected.extend(module.cases(stack, comparison=comparison))
        validate_cases(selected)
        unknown = set(groups) - {case.package for case in selected}
        if unknown:
            msg = f"unknown packages: {', '.join(sorted(unknown))}"
            raise RegistryError(msg)
        yield sorted(
            (case for case in selected if not groups or case.package in groups),
            key=lambda case: case.name,
        )


def validate_cases(cases: Sequence[Case]) -> None:
    """Reject duplicate IDs, unbounded descriptions and empty registries."""
    seen: set[str] = set()
    if not cases:
        msg = "empty speed registry"
        raise RegistryError(msg)
    for case in cases:
        if not case.name.startswith("unstd.") or len(case.name.split(".")) < 3:
            msg = f"invalid API name: {case.name}"
            raise RegistryError(msg)
        if case.name in seen:
            msg = f"duplicate speed case: {case.name}"
            raise RegistryError(msg)
        if not case.note.strip() or not callable(case.run) or not callable(case.check):
            msg = f"missing workload or oracle: {case.name}"
            raise RegistryError(msg)
        seen.add(case.name)


def _exports(path: Path) -> set[str]:
    """Read declared exports, or locally defined public functions/aliases."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fallback: set[str] = set()
    pending: list[ast.stmt] = list(tree.body)
    for node in pending:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "__all__":
                        return set(ast.literal_eval(node.value))
                    if not target.id.startswith("_"):
                        fallback.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                fallback.add(node.target.id)
        elif isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ) and not node.name.startswith("_"):
            fallback.add(node.name)
        elif isinstance(node, ast.If | ast.Try):
            pending.extend(node.body)
            pending.extend(node.orelse)
            if isinstance(node, ast.Try):
                pending.extend(node.finalbody)
                for handler in node.handlers:
                    pending.extend(handler.body)
    return fallback


def public_apis(root: Path = ROOT / "src/unstd") -> set[str]:
    """Discover public callables and locally owned methods; collapse reexports.

    Imported exception and typing vocabulary is not executable API work. A
    class's inherited third-party surface is owned by that backend, while its
    constructor and methods declared by unstd are owned here.
    """
    names: set[str] = set()
    seen: set[tuple[int, str]] = set()
    for package in sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    ):
        paths = sorted(
            package.rglob("*.py"),
            key=lambda path: (path.name != "__init__.py", path.name),
        )
        for path in paths:
            if any(
                part.startswith("_") for part in path.relative_to(package).parts[:-1]
            ) or (path.name.startswith("_") and path.name != "__init__.py"):
                continue
            relative = path.relative_to(root.parent).with_suffix("")
            module_name = ".".join(relative.parts).removesuffix(".__init__")
            exports = _exports(path)
            if not exports:
                # Provider module aliases own no callables here. In particular,
                # do not load an API absent from the running interpreter merely
                # to discover that its namespace has no package-owned exports.
                continue
            module = importlib.import_module(module_name)
            for name in sorted(exports):
                value = getattr(module, name, None)
                if not callable(value) or (
                    inspect.isclass(value) and issubclass(value, BaseException)
                ):
                    continue
                if getattr(value, "__module__", "") == "typing":
                    continue
                key = (id(value), name)
                if key in seen:
                    continue
                seen.add(key)
                qualified = f"{module_name}.{name}"
                names.add(qualified)
                if inspect.isclass(value) and value.__module__.startswith("unstd."):
                    for method, member in vars(value).items():
                        if method.startswith("_") and method not in {
                            "__enter__",
                            "__exit__",
                        }:
                            continue
                        if callable(member) or isinstance(
                            member, property | staticmethod | classmethod
                        ):
                            names.add(f"{qualified}.{method}")
                    for slot, annotation in getattr(
                        value, "__annotations__", {}
                    ).items():
                        if not slot.startswith("_") and "Callable" in str(annotation):
                            names.add(f"{qualified}.{slot}")
    return names


def check_coverage(
    cases: Sequence[Case], *, source: Path | None = None
) -> dict[str, str]:
    """Fail when an API/package has no case or an exclusion becomes stale."""
    validate_cases(cases)
    exclusions: dict[str, str] = {}
    for provider in _PROVIDERS:
        module = importlib.import_module(f"bench.workloads.speed_cases_{provider}")
        exclusions.update(getattr(module, "EXCLUSIONS", {}))
    apis = public_apis() if source is None else public_apis(source)
    covered = {case.name.split("#", 1)[0] for case in cases}
    missing = apis - covered - exclusions.keys()
    stale = exclusions.keys() - apis
    dangling = covered - apis
    packages = {
        path.name
        for path in (source or ROOT / "src/unstd").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    }
    absent = packages - {case.package for case in cases}
    if (
        missing
        or stale
        or dangling
        or absent
        or any(not reason.strip() for reason in exclusions.values())
    ):
        msg = f"speed coverage drift: missing={sorted(missing)}, stale exclusions={sorted(stale)}, unknown APIs={sorted(dangling)}, packages={sorted(absent)}"
        raise RegistryError(msg)
    return exclusions


@contextmanager
def _pin_cpu() -> Iterator[None]:
    """Keep separate revision processes on the same inherited logical CPU."""
    get_affinity = getattr(os, "sched_getaffinity", None)
    set_affinity = getattr(os, "sched_setaffinity", None)
    if get_affinity is None or set_affinity is None:
        yield
        return
    allowed = get_affinity(0)
    set_affinity(0, {min(allowed)})
    try:
        yield
    finally:
        set_affinity(0, allowed)


def environment() -> dict[str, object]:
    """Record the interpreter, CPU, optional backend versions and native routes."""
    cpu = platform.processor()
    if sys.platform == "darwin":
        cpu = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    elif Path("/proc/cpuinfo").is_file():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
    distributions = sorted(
        {
            requirement.split(">=")[0]
            for requirements in manifest["project"]["optional-dependencies"].values()
            for requirement in requirements
        }
    )
    versions: dict[str, str | None] = {}
    for distribution in distributions:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    native = {
        name: importlib.util.find_spec(f"unstd.{name}") is not None for name in _NATIVE
    }
    implementations = {
        name: "rust"
        if "Rust text kernels"
        in (importlib.import_module(f"unstd.{name}").__doc__ or "")
        else "c"
        for name, available in native.items()
        if available
    }
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        **(
            {"cpu_affinity": sorted(os.sched_getaffinity(0))}
            if hasattr(os, "sched_getaffinity")
            else {}
        ),
        "backends": versions,
        "native": native,
        "native_implementations": implementations,
        "python_debug": sysconfig.get_config_var("Py_DEBUG"),
        "python_free_threaded": sysconfig.get_config_var("Py_GIL_DISABLED"),
        "thread_settings": {
            key: os.environ.get(key)
            for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
        },
    }


def workload_sources() -> dict[str, str]:
    """Fingerprint workload fixtures so edited payloads cannot compare silently."""
    return {
        f"speed_cases_{provider}.py": hashlib.sha256(
            (ROOT / f"bench/workloads/speed_cases_{provider}.py").read_bytes()
        ).hexdigest()
        for provider in _PROVIDERS
    }


def provenance() -> dict[str, object]:
    """Retain loaded module files and build inputs outside comparison ceilings."""
    files = {}
    for name, module in sorted(sys.modules.copy().items()):
        if not (
            name == "unstd"
            or name.startswith(("unstd.", "zlib_ng."))
            or name in {"gzip", "zlib", "_compression"}
        ):
            continue
        filename = getattr(module, "__file__", None)
        if filename is not None:
            path = Path(filename).resolve()
            files[name] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {
        "loaded_files": files,
        "workload_sources": workload_sources(),
        "harness_sources": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("bench/speeds.py", "bench/core/timing.py")
        },
        "python_executable": sys.executable,
        "python_build": sys.version,
        "compiler_configuration": {
            key: sysconfig.get_config_var(key)
            for key in ("CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS", "CONFIG_ARGS")
        },
        "build_environment": {
            key: os.environ.get(key)
            for key in (
                "CC",
                "CXX",
                "CFLAGS",
                "CPPFLAGS",
                "LDFLAGS",
                "CIBUILDWHEEL",
                "UNSTD_TEXT_RUST",
                "UNSTD_GZIP_ZLIB_RS",
                "PYTHONMALLOC",
            )
        },
    }


def _validate_document(raw: Any) -> None:
    """Check all fields needed to verify or extend persisted evidence."""
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema")) is not int
        or raw["schema"] != _SCHEMA
    ):
        _invalid("invalid schema")
    if not isinstance(raw.get("environment"), dict) or not raw["environment"]:
        _invalid("invalid environment")
    if not isinstance(raw.get("cases"), dict) or not raw["cases"]:
        _invalid("invalid cases")
    if not isinstance(raw.get("recorded"), str) or not raw["recorded"]:
        _invalid("missing recording timestamp")
    tolerance = raw.get("tolerance")
    if (
        not isinstance(tolerance, int | float)
        or isinstance(tolerance, bool)
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 1
    ):
        _invalid("invalid tolerance")
    sources = raw.get("workload_sources")
    if (
        not isinstance(sources, dict)
        or not sources
        or any(
            not isinstance(value, str) or len(value) != 64 for value in sources.values()
        )
    ):
        _invalid("invalid workload source fingerprints")
    for name, row in raw["cases"].items():
        if (
            not isinstance(name, str)
            or not isinstance(row, dict)
            or not isinstance(row.get("note"), str)
            or not row["note"].strip()
        ):
            _invalid("invalid workload identity")
        for key in ("measured_ns", "ceiling_ns", "spread"):
            value = row.get(key)
            if (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
                or (key != "spread" and value == 0)
            ):
                _invalid(f"invalid {key} for {name}")
        for key in ("iterations", "rounds"):
            if type(row.get(key)) is not int or row[key] <= 0:
                _invalid(f"invalid {key} for {name}")
        if row["ceiling_ns"] < row["measured_ns"]:
            _invalid(f"ceiling below measurement for {name}")


def load_baseline(path: Path = BASELINE) -> dict[str, Any]:
    """Read a complete finite, positive latency baseline; never default to pass."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _validate_document(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        msg = f"invalid speed baseline {path}: {error}"
        raise RegistryError(msg) from error
    return dict(raw)


def validate_baseline(
    cases: Sequence[Case], baseline: dict[str, Any], *, exact: bool = True
) -> None:
    """Require every selected case to have the same frozen workload contract."""
    names = {case.name for case in cases}
    if baseline["workload_sources"] != workload_sources():
        msg = "workload source changed; review the benchmark changes and freeze to acknowledge them without relaxing existing ceilings"
        raise RegistryError(msg)
    rows = baseline["cases"]
    missing = names - rows.keys()
    stale = rows.keys() - names if exact else set()
    changed = [
        case.name
        for case in cases
        if case.name in rows and rows[case.name]["note"] != case.note
    ]
    if missing or stale or changed:
        msg = f"baseline coverage drift: missing={sorted(missing)}, stale={sorted(stale)}, changed workloads={changed}"
        raise RegistryError(msg)


def revision_cases(
    cases: Sequence[Case], baseline: dict[str, Any]
) -> tuple[list[Case], list[str]]:
    """Compare every baseline workload and report APIs absent from that revision."""
    shared = [case for case in cases if case.name in baseline["cases"]]
    validate_baseline(shared, baseline)
    if not shared:
        _invalid("revisions have no shared workloads")
    added = sorted(case.name for case in cases if case.name not in baseline["cases"])
    return shared, added


def comparable_environment(
    before: dict[str, Any], current: dict[str, Any], *, revision: bool = False
) -> bool:
    """Keep runtime checks strict while recording revision-owned native changes."""
    ignored = {"native", "native_implementations"} if revision else set()
    return {key: value for key, value in before.items() if key not in ignored} == {
        key: value for key, value in current.items() if key not in ignored
    }


def audit(cases: Sequence[Case]) -> None:
    """Run independent correctness oracles before reporting any speed."""
    for case in cases:
        try:
            case.check(case.run())
        except Exception as error:
            msg = f"{case.name}: correctness audit failed: {error}"
            raise RegistryError(msg) from error


def timings(cases: Sequence[Case], *, rounds: int = 7) -> dict[str, Measurement]:
    """Check before and after timing so a no-op cannot buy an apparent speedup."""
    if rounds < 3:
        msg = "at least three timing rounds are required"
        raise RegistryError(msg)
    audit(cases)
    measured = {case.name: measure(case.run, rounds=rounds) for case in cases}
    audit(cases)
    return measured


def freeze(
    cases: Sequence[Case],
    measured: dict[str, Measurement],
    path: Path = BASELINE,
    *,
    tolerance: float = 0.30,
) -> dict[str, Any]:
    """Add measured cases; retain existing ceilings and evidence unchanged."""
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        msg = "tolerance must be between 0 and 1"
        raise RegistryError(msg)
    current_environment = environment()
    prior = load_baseline(path) if path.exists() else None
    if prior is not None and prior["environment"] != current_environment:
        msg = "baseline environment differs; use a separate --baseline file for this worker"
        raise RegistryError(msg)
    if prior is not None:
        # Freeze is append-only. Existing evidence must not be rewritten to
        # excuse a slowdown, including through a narrowed package selection.
        for case in cases:
            if (
                case.name in prior["cases"]
                and prior["cases"][case.name]["note"] != case.note
            ):
                msg = f"changed frozen workload: {case.name}"
                raise RegistryError(msg)
    rows = dict(prior["cases"]) if prior else {}
    for case in cases:
        result = measured[case.name]
        if not math.isfinite(result.per_op_ns) or result.per_op_ns <= 0:
            msg = f"invalid measurement: {case.name}"
            raise RegistryError(msg)
        if case.name not in rows:
            rows[case.name] = {
                "measured_ns": result.per_op_ns,
                "ceiling_ns": result.per_op_ns * (1 + tolerance),
                "note": case.note,
                "iterations": result.iters,
                "rounds": result.rounds,
                "spread": result.spread,
            }
    raw = {
        "schema": _SCHEMA,
        "recorded": prior["recorded"] if prior else datetime.now(UTC).isoformat(),
        "environment": current_environment,
        "tolerance": prior["tolerance"] if prior else tolerance,
        "workload_sources": workload_sources(),
        "cases": dict(sorted(rows.items())),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return raw


def regressions(
    measured: dict[str, Measurement], baseline: dict[str, Any]
) -> list[str]:
    """Return cases slower than their frozen ceiling; absent rows are errors."""
    failures = []
    for name, result in measured.items():
        if name not in baseline["cases"]:
            msg = f"missing frozen speed: {name}"
            raise RegistryError(msg)
        if not math.isfinite(result.per_op_ns) or result.per_op_ns <= 0:
            msg = f"invalid measurement: {name}"
            raise RegistryError(msg)
        if result.per_op_ns > baseline["cases"][name]["ceiling_ns"]:
            failures.append(name)
    return failures


def recheck_regressions(
    cases: Sequence[Case],
    measured: dict[str, Measurement],
    baseline: dict[str, Any],
    *,
    rounds: int = 7,
    attempts: int = 2,
) -> tuple[dict[str, Measurement], dict[str, list[Measurement]]]:
    """Require a slowdown to persist across bounded additional measurements.

    Keep the least contaminated batch, using the same estimator as initial
    timing. Persistent slowdowns still fail every attempt; retained attempts
    reveal transient load instead of silently changing the speed ceiling.
    """
    best = dict(measured)
    history = {name: [result] for name, result in measured.items()}
    by_name = {case.name: case for case in cases}
    for _ in range(attempts):
        failed = regressions(best, baseline)
        if not failed:
            break
        fresh = timings([by_name[name] for name in failed], rounds=rounds)
        for name, result in fresh.items():
            history[name].append(result)
            if result.per_op_ns < best[name].per_op_ns:
                best[name] = result
    return best, history


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=("list", "check", "run", "freeze", "verify"))
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, help="retain measurements as JSON")
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument(
        "--comparison-source",
        type=Path,
        help="freeze an older revision using its src/unstd API inventory",
    )
    parser.add_argument(
        "--revision-baseline",
        action="store_true",
        help="verify shared workloads and report newly added APIs/native routes",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.30,
        help="headroom for newly frozen cases (default 30%%)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Return 1 for speed regressions and 2 for missing/incomparable evidence."""
    args = _parser().parse_args(argv)
    try:
        if args.comparison_source is not None and args.verb != "freeze":
            _invalid("--comparison-source requires freeze")
        if args.revision_baseline and args.verb != "verify":
            _invalid("--revision-baseline requires verify")
        if (
            args.comparison_source is not None or args.revision_baseline
        ) and args.group:
            _invalid("revision comparisons require the complete registry")
        prepared = (
            registry(comparison=True)
            if args.comparison_source is not None
            else registry()
        )
        with _pin_cpu(), prepared as all_cases:
            exclusions = (
                check_coverage(all_cases, source=args.comparison_source)
                if args.comparison_source is not None
                else check_coverage(all_cases)
            )
            selected = [
                case
                for case in all_cases
                if not args.group or case.package in args.group
            ]
            unknown = set(args.group) - {case.package for case in all_cases}
            baseline: dict[str, Any] | None
            if unknown:
                msg = f"unknown packages: {sorted(unknown)}"
                _invalid(msg)
            if args.verb == "list":
                for case in selected:
                    print(f"{case.name}\t{case.note}")
                return 0
            if args.verb == "check":
                baseline = load_baseline(args.baseline)
                validate_baseline(all_cases, baseline)
                audit(selected)
                print(
                    f"{len(selected)} workloads, {len({case.package for case in selected})} packages, {len(exclusions)} documented vocabulary exclusions; coverage and correctness verified"
                )
                return 0
            baseline = None
            added: list[str] = []
            comparable = selected
            # Both phases load metadata/native routes before the first timing.
            # Freeze used to defer this work until after its measurements.
            current_environment = environment()
            if args.verb == "verify":
                baseline = load_baseline(args.baseline)
                if args.revision_baseline:
                    shared, added = revision_cases(all_cases, baseline)
                    shared_names = {case.name for case in shared}
                    comparable = [
                        case for case in selected if case.name in shared_names
                    ]
                else:
                    validate_baseline(all_cases, baseline)
                if not comparable_environment(
                    baseline["environment"],
                    current_environment,
                    revision=args.revision_baseline,
                ):
                    msg = "baseline environment differs; freeze a separate --baseline file on this worker before comparing revisions"
                    _invalid(msg)
            current_provenance = provenance()
            # Compare the same workload history as the older revision. Added
            # APIs must not precondition shared allocation/caches in this pass.
            measured = timings(comparable, rounds=args.rounds)
            attempts = {name: [result] for name, result in measured.items()}
            if args.verb == "verify":
                assert baseline is not None
                checked, history = recheck_regressions(
                    comparable,
                    {case.name: measured[case.name] for case in comparable},
                    baseline,
                    rounds=args.rounds,
                )
                measured.update(checked)
                attempts.update(history)
            if added:
                additions = [case for case in selected if case.name in added]
                fresh = timings(additions, rounds=args.rounds)
                measured.update(fresh)
                attempts.update({name: [result] for name, result in fresh.items()})
            if args.verb == "freeze":
                baseline = freeze(
                    selected, measured, args.baseline, tolerance=args.tolerance
                )
            failed = (
                regressions(
                    {case.name: measured[case.name] for case in comparable}, baseline
                )
                if baseline
                else []
            )
            rows = {}
            for case in selected:
                result = measured[case.name]
                ceiling = (
                    baseline["cases"][case.name]["ceiling_ns"]
                    if baseline and case.name in baseline["cases"]
                    else None
                )
                verdict = (
                    "FAIL"
                    if case.name in failed
                    else "added"
                    if case.name in added
                    else "ok"
                )
                noise = " noisy" if result.spread > 0.25 else ""
                print(f"{case.name:<54} {result.human():>11} {verdict}{noise}")
                rows[case.name] = {
                    "ns": result.per_op_ns,
                    "ceiling_ns": ceiling,
                    "spread": result.spread,
                    "rounds": result.rounds,
                    "iterations": result.iters,
                    "failed": case.name in failed,
                    "samples_ns": result.samples_ns,
                    "cpu_samples_ns": result.cpu_samples_ns,
                    "attempts_ns": [
                        attempt.per_op_ns for attempt in attempts[case.name]
                    ],
                    "attempts": [
                        {
                            "ns": attempt.per_op_ns,
                            "iterations": attempt.iters,
                            "spread": attempt.spread,
                            "samples_ns": attempt.samples_ns,
                            "cpu_samples_ns": attempt.cpu_samples_ns,
                        }
                        for attempt in attempts[case.name]
                    ],
                }
            if args.output:
                report = {
                    "environment": current_environment,
                    "provenance": current_provenance,
                    "baseline_sha256": hashlib.sha256(
                        args.baseline.read_bytes()
                    ).hexdigest()
                    if baseline
                    else None,
                    "cases": rows,
                    "failed": failed,
                    "added": added,
                    "baseline_environment": baseline["environment"]
                    if baseline
                    else None,
                }
                args.output.write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )
            print(
                f"{len(measured)} measured; {len(added)} added; {len(failed)} regressions"
            )
            return 1 if failed else 0
    except (RegistryError, ImportError, OSError) as error:
        print(f"speeds: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
