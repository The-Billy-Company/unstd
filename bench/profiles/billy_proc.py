"""Process contracts, profiled separately from paired full-call timings.

Run this file directly. Disposable Git and pkg-config fixtures replay current
consumer call shapes. Large text blobs are explicit scaling fixtures, not observed
production percentiles; asynchronous binary workers are outside this API.
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
from tempfile import TemporaryDirectory
import time
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]
_RECEIPTS = _ROOT / "bench/workloads/billy_proc_receipts.json"
_MODES = ("baseline", "candidate", "python", "stdlib", "spawn", "io")


def _configure_mode(backend: ModuleType, mode: str) -> Any:
    """Select kernels only within an isolated worker, before audit or timing."""
    if mode == "python":
        vars(backend)["_native"] = None
    if mode in {"spawn", "io"}:
        vars(backend)["_SPAWN_NATIVE"] = mode == "spawn"
    native = getattr(backend, "_native", None) if mode != "stdlib" else None
    if native is not None and mode in {"spawn", "io"}:
        disabled = "communicate" if mode == "spawn" else "spawn"

        def decline(*args: Any, **kwargs: Any) -> Any:
            return NotImplemented

        setattr(native, disabled, decline)
    return native


def _expected_native(
    mode: str, *, spawn_enabled: bool, stdin_text: bool
) -> tuple[str, ...]:
    """Admitted kernels follow the platform policy and the public input shape."""
    expected = []
    if mode == "spawn" or (mode == "candidate" and spawn_enabled):
        expected.append("spawn")
    if mode in {"candidate", "io"} and not stdin_text:
        expected.append("communicate")
    return tuple(expected)


def _verify_routes(
    mode: str,
    routes: dict[str, dict[str, int]],
    *,
    require_native: bool,
    case: str,
    spawn_enabled: bool | None = None,
    stdin_text: bool = False,
) -> None:
    """Reject inactive kernels and accidental native work in isolated controls."""
    if mode == "baseline":
        return
    if spawn_enabled is None:
        spawn_enabled = sys.platform == "darwin"
    expected = _expected_native(
        mode, spawn_enabled=spawn_enabled, stdin_text=stdin_text
    )
    for kernel in ("spawn", "communicate"):
        counts = routes.get(kernel, {})
        if kernel in expected:
            if require_native and not counts.get("accepted", 0):
                message = f"{mode} did not successfully use native {kernel}: {case}"
                raise RuntimeError(message)
        elif counts.get("accepted", 0):
            message = f"native {kernel} leaked into the {mode} control: {case}"
            raise RuntimeError(message)
    if mode == "io" or (mode == "candidate" and not spawn_enabled):
        counts = routes.get("spawn", {})
        if counts.get("calls", sum(counts.values())):
            message = f"disabled native spawn was entered in {mode}: {case}"
            raise RuntimeError(message)
    if (
        require_native
        and mode == "spawn"
        and not stdin_text
        and not routes.get("communicate", {}).get("declined", 0)
    ):
        message = f"spawn did not prove native communicate was declined: {case}"
        raise RuntimeError(message)


@dataclass(frozen=True)
class Workload:
    """One frozen command and independently specified text-result contract."""

    name: str
    argv: tuple[str, ...]
    stdout: str | None = ""
    stderr: str = ""
    api: str = "run"
    iterations: int = 6
    options: dict[str, Any] | None = None
    receipts: tuple[str, ...] = ()
    scope: str = "current Billy command shape over a disposable fixture"


def _sha(raw: bytes) -> str:
    """An identity for source or fixture bytes, outside timed work."""
    return hashlib.sha256(raw).hexdigest()


def _environment(root: Path) -> dict[str, str]:
    """Keep tool behavior hermetic without recording inherited environment values."""
    inherited = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("GIT_", "PKG_CONFIG_"))
    }
    return inherited | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": "Proc Fixture",
        "GIT_AUTHOR_EMAIL": "proc@example.invalid",
        "GIT_COMMITTER_NAME": "Proc Fixture",
        "GIT_COMMITTER_EMAIL": "proc@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        "PKG_CONFIG_LIBDIR": str(root / "pkgconfig"),
        "PKG_CONFIG_PATH": str(root / "pkgconfig"),
        "PYTHONUTF8": "1",
    }


def _command(argv: list[str], root: Path, *, input_text: str | None = None) -> str:
    """Run only fixture preparation commands through an independent stdlib oracle."""
    result = subprocess.run(  # noqa: S603 — fixed local fixture argv, no shell/network
        argv,
        cwd=root,
        env=_environment(root.parent),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=True,
        timeout=30,
    )
    return result.stdout


def _payload(size: int) -> bytes:
    """Deterministic UTF-8, NUL and newline forms exercise captured-text decoding."""
    unit = "trace café ☃\r\nnext\rend\x00\n".encode()
    return (unit * (size // len(unit) + 1))[:size]


def _decoded(raw: bytes) -> str:
    """The independently specified proc text contract, including universal lines."""
    return (
        raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    )


def _prepare_fixture(directory: Path) -> dict[str, Any]:
    """Create local-only Git history and package metadata before worker timing."""
    root = directory.resolve()
    repository = root / "repository"
    repository.mkdir(parents=True)
    git = shutil.which("git")
    if git is None:
        message = "Billy source fixtures require an installed git executable"
        raise RuntimeError(message)
    _command([git, "init", "--quiet"], repository)
    _command([git, "config", "core.autocrlf", "false"], repository)
    _command([git, "config", "core.quotePath", "false"], repository)
    tracked = []
    for index in range(128):
        name = f"services/ai/fixture_{index:03}.py"
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"value = {index}\n", encoding="utf-8")
        tracked.append(name)
    unicode_name = "tools/café fixture.py"
    (repository / "tools").mkdir()
    (repository / unicode_name).write_text("value = '☃'\n", encoding="utf-8")
    tracked.append(unicode_name)
    plans = repository / "plans"
    plans.mkdir()
    for size in (1 << 20, 16 << 20):
        (plans / f"plan-{size >> 20}mib.txt").write_bytes(_payload(size))
    _command([git, "add", "--all"], repository)
    tree = _command([git, "write-tree"], repository).strip()
    commit = _command(
        [git, "commit-tree", tree], repository, input_text="fixture\n"
    ).strip()
    _command([git, "update-ref", "HEAD", commit], repository)
    clean = root / "clean-repository"
    clean.mkdir()
    _command([git, "init", "--quiet"], clean)
    # Leave the source fixture stable but dirty; status and numstat exercise
    # actual source-admission shapes without modifying it during measurement.
    (repository / tracked[0]).write_text("value = 999\n", encoding="utf-8")
    extra = "services/ai/untracked.txt"
    (repository / extra).write_text("fixture\n", encoding="utf-8")
    inventory = "".join(name + "\0" for name in [extra, *tracked])
    pkgconfig = root / "pkgconfig"
    pkgconfig.mkdir()
    libdir = str(root / "library with spaces")
    (pkgconfig / "rubberband.pc").write_text(
        f"prefix={root}\nlibdir={libdir}\nName: rubberband\n"
        "Description: hermetic proc fixture\nVersion: 1.0\n",
        encoding="utf-8",
    )
    fixture = {
        "root": str(root),
        "repository": str(repository),
        "clean_repository": str(clean),
        "git": git,
        "commit": commit,
        "tracked_source_files": len(tracked),
        "inventory": inventory,
        "libdir": libdir,
        "payload_sha256": {
            str(size): _sha(_payload(size)) for size in (1 << 20, 16 << 20)
        },
    }
    # Git's documented ordering combines untracked and cached inventories.
    # Independently enumerate all fixture paths, then verify the oracle before
    # timing: a changed Git default cannot quietly change the workload contract.
    observed = _command(
        [
            git,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "services",
            "tools",
        ],
        repository,
    )
    expected_names = {extra, *tracked}
    if set(observed.rstrip("\0").split("\0")) != expected_names:
        message = "git inventory does not match independently created fixture paths"
        raise RuntimeError(message)
    fixture["inventory"] = observed
    manifest = root / "fixture.json"
    manifest.write_text(json.dumps(fixture), encoding="utf-8")
    return fixture


def _workloads(fixture: dict[str, Any]) -> tuple[list[Workload], dict[str, str]]:
    """Replay receipt-backed current commands, with explicit synthetic controls."""
    root = Path(fixture["root"])
    repository = str(fixture["repository"])
    git = str(fixture["git"])
    common: dict[str, Any] = {"cwd": repository, "timeout": 5, "check": False}
    cases = [
        Workload(
            "git-revision-cwd",
            ("git", "rev-parse", "HEAD"),
            fixture["commit"] + "\n",
            options=common | {"timeout": 1},
            receipts=("source-revision",),
        ),
        Workload(
            "git-revision-no-cwd",
            (git, "-C", repository, "rev-parse", "HEAD"),
            fixture["commit"] + "\n",
            options={"timeout": 5, "check": False},
            receipts=("replay-revision", "media-decision-revision"),
        ),
        Workload(
            "git-inventory-nul",
            (
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "services",
                "tools",
            ),
            fixture["inventory"],
            options=common | {"check": True},
            receipts=("inventory",),
        ),
        Workload(
            "git-status-clean",
            ("git", "status", "--porcelain", "--untracked-files=normal"),
            "",
            api="capture",
            options=common | {"cwd": fixture["clean_repository"], "check": True},
            receipts=("source-status",),
        ),
        Workload(
            "git-status-dirty",
            ("git", "status", "--porcelain", "--untracked-files=normal"),
            " M services/ai/fixture_000.py\n?? services/ai/untracked.txt\n",
            api="capture",
            options=common | {"check": True},
            receipts=("source-status",),
        ),
        Workload(
            "git-diff-numstat",
            (
                "git",
                "-c",
                "diff.autoRefreshIndex=false",
                "diff",
                "--numstat",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                fixture["commit"],
                "--",
                "services",
                "tools",
            ),
            "1\t1\tservices/ai/fixture_000.py\0",
            options=common | {"check": True},
            receipts=("diff-inventory",),
        ),
    ]
    cases.extend(
        Workload(
            f"git-show-{size >> 20}mib",
            (git, "show", f"HEAD:plans/plan-{size >> 20}mib.txt"),
            _decoded(_payload(size)),
            iterations=2,
            options=common,
            receipts=("approved-plan",),
            scope="current git-show shape; synthetic text scaling, not production size evidence",
        )
        for size in (1 << 20, 16 << 20)
    )
    skipped = {}
    pkgconfig = shutil.which("pkg-config")
    if pkgconfig is not None:
        cases.append(
            Workload(
                "pkg-config-libdir",
                (pkgconfig, "--variable=libdir", "rubberband"),
                fixture["libdir"] + "\n",
                options={"timeout": 2, "check": False, "env": _environment(root)},
                receipts=("pkg-config",),
            )
        )
    else:
        skipped["pkg-config-libdir"] = "pkg-config executable is unavailable"
    ps = shutil.which("ps")
    if ps is not None:
        cases.append(
            Workload(
                "ps-process-inventory",
                ("ps", "-axo", "uid=,pid=,ppid=,command="),
                None,
                iterations=3,
                options={"timeout": 5, "errors": "strict"},
                receipts=("process-inventory",),
                scope="current read-only ps command; dynamic output checked structurally, cleanup never invoked",
            )
        )
    else:
        skipped["ps-process-inventory"] = "ps executable is unavailable"
    python = (sys.executable, "-S", "-c")
    log = "qualification café ☃\n" * 32768
    cases.append(
        Workload(
            "qualification-log-shape",
            (
                *python,
                "import sys; line='qualification café ☃\\n'; sys.stdout.write(line*32768); sys.stderr.write(line*32768)",
            ),
            log,
            log,
            iterations=2,
            options=common | {"env": _environment(root)},
            receipts=("native-qualification-logs",),
            scope="synthetic qualification stdout/stderr shape; excludes native test/build work",
        )
    )
    cat = shutil.which("cat")
    if cat is not None:
        cases.append(
            Workload(
                "stdin-text-stress-1mib",
                (cat,),
                _decoded(_payload(1 << 20)),
                iterations=2,
                options={"timeout": 10, "stdin_text": _decoded(_payload(1 << 20))},
                scope="synthetic backend stress; no current Billy proc stdin_text caller found",
            )
        )
    return cases, skipped


def _check(result: Any, case: Workload) -> None:
    """Verify exact fixture output or an explicit dynamic-process-table oracle."""
    if case.api == "capture":
        if type(result) is str and result == (case.stdout or "").strip():
            return
    elif (
        result.argv == case.argv
        and result.returncode == 0
        and type(result.stdout) is str
        and type(result.stderr) is str
        and result.stderr == case.stderr
    ):
        if case.stdout is not None and result.stdout == case.stdout:
            return
        if case.stdout is None:
            rows = [line.strip().split(None, 3) for line in result.stdout.splitlines()]
            if (
                rows
                and all(
                    len(row) == 4 and all(value.isdecimal() for value in row[:3])
                    for row in rows
                )
                and any(int(row[1]) == os.getpid() for row in rows)
            ):
                return
    message = f"captured result disagrees with independent Billy fixture: {case.name}"
    raise RuntimeError(message)


def _stdlib_call(api: str, argv: tuple[str, ...], **options: Any) -> Any:
    """A like-for-like stdlib control for captured decoded process output."""
    kwargs = options.copy()
    kwargs["input"] = kwargs.pop("stdin_text", None)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    child = subprocess.run(argv, capture_output=True, text=True, **kwargs)  # noqa: S603 — frozen fixture argv
    if api == "capture":
        return child.stdout.strip()
    return SimpleNamespace(
        argv=argv, returncode=child.returncode, stdout=child.stdout, stderr=child.stderr
    )


def _profile(call: Callable[..., Any], case: Workload, native: Any) -> dict[str, Any]:
    """Observe successful native routes without adding instrumentation to timing."""
    routes: dict[str, dict[str, int]] = {}
    originals = {}
    if native is not None:
        for name in ("spawn", "communicate"):
            operation = getattr(native, name, None)
            if operation is None:
                continue
            originals[name] = operation
            counts = {"calls": 0, "accepted": 0, "declined": 0, "raised": 0}
            routes[name] = counts

            def observe(
                *args: Any,
                _operation: Callable[..., Any] = operation,
                _counts: dict[str, int] = counts,
                **kwargs: Any,
            ) -> Any:
                _counts["calls"] += 1
                try:
                    result = _operation(*args, **kwargs)
                except BaseException:
                    _counts["raised"] += 1
                    raise
                _counts["declined" if result is NotImplemented else "accepted"] += 1
                return result

            setattr(native, name, observe)
    profiler = cProfile.Profile()
    try:
        cpu_start = time.process_time_ns()
        result = profiler.runcall(call, case.argv, **(case.options or {}))
        cpu_ns = time.process_time_ns() - cpu_start
    finally:
        for name, original in originals.items():
            setattr(native, name, original)
    _check(result, case)
    entries = []
    for entry in profiler.getstats():
        code = entry.code
        location = (
            code
            if isinstance(code, str)
            else f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
        )
        if any(
            token in location
            for token in (
                "spawn",
                "fork",
                "execute_child",
                "communicate",
                "posix.read",
                "posix.write",
                "wait",
                "poll",
                "select",
                "sleep",
                "kqueue",
            )
        ):
            entries.append(
                {
                    "location": location,
                    "calls": entry.callcount,
                    "total_seconds": entry.totaltime,
                    "self_seconds": entry.inlinetime,
                }
            )
    return {
        "parent_cpu_ns": cpu_ns,
        "native_routes": routes,
        "entries": entries,
        "timer_note": "cProfile seconds include blocking wall time; process CPU is reported separately",
    }


def _measure(call: Callable[..., Any], case: Workload, native: Any) -> dict[str, Any]:
    """Time complete public calls, restoring GC even when a process fails."""
    profile = _profile(call, case, native)
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
        _check(result, case)
    return {
        "iterations": case.iterations,
        "wall_ns": wall_ns,
        "parent_cpu_ns": cpu_ns,
        "wall_ns_per_call": wall_ns / case.iterations,
        "parent_cpu_ns_per_call": cpu_ns / case.iterations,
        "output_utf8_bytes": len((case.stdout or "").encode())
        + len(case.stderr.encode()),
        "profile": profile,
        "receipts": case.receipts,
        "scope": case.scope,
        "exact_output_oracle": case.stdout is not None,
    }


def _source_hashes(source: Path) -> dict[str, str]:
    """Fingerprint selected Python sources, stubs and optional native binaries."""
    package = source / "unstd/proc"
    if not (package / "__init__.py").is_file():
        message = f"source must contain unstd/proc/__init__.py: {source}"
        raise ValueError(message)
    return {
        str(path.relative_to(source)): _sha(path.read_bytes())
        for path in sorted(package.rglob("*"))
        if path.is_file() and (path.suffix in {".py", ".pyi", ".so", ".pyd", ".dylib"})
    }


def _validate_receipts(cases: list[Workload]) -> None:
    """Every claimed current call shape must refer to a frozen source receipt."""
    receipt = json.loads(_RECEIPTS.read_text(encoding="utf-8"))
    known = {item["id"] for item in receipt["callsites"]}
    missing = {identifier for case in cases for identifier in case.receipts} - known
    if missing:
        message = f"workloads cite unknown Billy source receipts: {sorted(missing)}"
        raise RuntimeError(message)


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    """Select a proc source before import and apply only worker-local controls."""
    source = args.source.resolve()
    hashes = _source_hashes(source)
    if any(name == "unstd" or name.startswith("unstd.") for name in sys.modules):
        message = "worker imported unstd before source selection"
        raise RuntimeError(message)
    root = ModuleType("unstd")
    root.__path__ = [str(source / "unstd")]
    sys.modules["unstd"] = root
    proc = importlib.import_module("unstd.proc")
    filename = proc.__file__
    if (
        filename is None
        or Path(filename).resolve() != source / "unstd/proc/__init__.py"
    ):
        message = "worker loaded the wrong proc source"
        raise RuntimeError(message)
    backend = (
        sys.modules.get("unstd.proc.backend")
        or sys.modules["unstd.proc._internal.backend"]
    )
    native = _configure_mode(backend, args.mode)
    spawn_enabled = bool(getattr(backend, "_SPAWN_NATIVE", True))
    if (
        args.require_native
        and args.mode in {"candidate", "spawn", "io"}
        and native is None
    ):
        message = f"{args.mode} did not load the native extension"
        raise RuntimeError(message)
    native_file = getattr(native, "__file__", None)
    if native_file is not None and not Path(native_file).resolve().is_relative_to(
        source / "unstd/proc"
    ):
        message = "worker loaded a native binary outside its selected proc source"
        raise RuntimeError(message)
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    environment = _environment(Path(fixture["root"]))
    os.environ.clear()
    os.environ.update(environment)
    cases, skipped = _workloads(fixture)
    _validate_receipts(cases)
    known = {case.name for case in cases} | skipped.keys()
    if set(args.case) - known:
        message = f"unknown Billy proc workloads: {sorted(set(args.case) - known)}"
        raise ValueError(message)
    measurements = {}
    for case in cases:
        if args.case and case.name not in args.case:
            continue
        call = (
            (lambda argv, _api=case.api, **kw: _stdlib_call(_api, argv, **kw))
            if args.mode == "stdlib"
            else getattr(proc, case.api)
        )
        measured = _measure(call, case, native)
        routes = measured["profile"]["native_routes"]
        stdin_text = (case.options or {}).get("stdin_text") is not None
        _verify_routes(
            args.mode,
            routes,
            require_native=args.require_native,
            case=case.name,
            spawn_enabled=spawn_enabled,
            stdin_text=stdin_text,
        )
        measured["expected_native_kernels"] = _expected_native(
            args.mode, spawn_enabled=spawn_enabled, stdin_text=stdin_text
        )
        measured["native_claim_eligible"] = bool(measured["expected_native_kernels"])
        if stdin_text and not measured["native_claim_eligible"]:
            measured["native_coverage_note"] = (
                "unaccelerated stdin stress; stdlib fallback, no native gain claimed"
            )
        measurements[case.name] = measured
    if hashes != _source_hashes(source):
        message = "proc source changed during a measured worker"
        raise RuntimeError(message)
    return {
        "mode": args.mode,
        "native_spawn_policy": spawn_enabled,
        "harness_sha256": _sha(Path(__file__).read_bytes()),
        "source": str(source),
        "source_hashes": hashes,
        "imported_source": str(Path(filename).resolve()),
        "native_binary": None
        if native_file is None
        else {
            "path": str(Path(native_file).resolve()),
            "sha256": _sha(Path(native_file).read_bytes()),
        },
        "signatures": {
            name: str(inspect.signature(getattr(proc, name)))
            for name in ("run", "capture", "run_ok")
        },
        "measurements": measurements,
        "skipped": {
            key: value
            for key, value in skipped.items()
            if not args.case or key in args.case
        },
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Alternate all selected controls over one immutable disposable fixture."""
    modes = args.modes.split(",")
    if (
        len(set(modes)) != len(modes)
        or any(mode not in _MODES for mode in modes)
        or "baseline" not in modes
    ):
        message = "modes must be unique baseline/candidate/python/stdlib/spawn/io values including baseline"
        raise ValueError(message)
    if args.require_native and not {"candidate", "spawn", "io"}.intersection(modes):
        message = (
            "--require-native needs candidate, spawn or io among the measured modes"
        )
        raise ValueError(message)
    harness_hash = _sha(Path(__file__).read_bytes())
    receipts_hash = _sha(_RECEIPTS.read_bytes())
    sources = {
        mode: (args.baseline_source if mode == "baseline" else args.source).resolve()
        for mode in modes
    }
    hashes = {mode: _source_hashes(source) for mode, source in sources.items()}
    samples: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    with TemporaryDirectory(prefix="billy-proc-fixture-") as temporary:
        fixture = _prepare_fixture(Path(temporary))
        for index in range(args.rounds):
            offset = index % len(modes)
            order = modes[offset:] + modes[:offset]
            if (index // len(modes)) % 2:
                order.reverse()
            for mode in order:
                command = [
                    sys.executable,
                    "-S",
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--source",
                    str(sources[mode]),
                    "--fixture",
                    str(Path(temporary) / "fixture.json"),
                    "--mode",
                    mode,
                ]
                for case in args.case:
                    command.extend(("--case", case))
                if args.require_native:
                    command.append("--require-native")
                worker = subprocess.run(  # noqa: S603 — isolated local harness worker
                    command, capture_output=True, text=True, timeout=120, check=False
                )
                if worker.returncode:
                    message = (
                        f"{mode} benchmark worker failed:\n{worker.stderr[-10000:]}"
                    )
                    raise RuntimeError(message)
                sample = json.loads(worker.stdout)
                if sample["harness_sha256"] != harness_hash:
                    message = "benchmark harness changed across paired workers"
                    raise RuntimeError(message)
                if sample["source_hashes"] != hashes[mode]:
                    message = f"{mode} source changed across paired rounds"
                    raise RuntimeError(message)
                samples[mode].append(sample)
            signatures = samples["baseline"][-1]["signatures"]
            if any(samples[mode][-1]["signatures"] != signatures for mode in modes):
                message = "public proc signatures changed between sources"
                raise RuntimeError(message)
    if harness_hash != _sha(Path(__file__).read_bytes()) or receipts_hash != _sha(
        _RECEIPTS.read_bytes()
    ):
        message = "benchmark harness or consumer receipts changed during the run"
        raise RuntimeError(message)
    summary = {}
    for name in samples["baseline"][0]["measurements"]:
        by_mode = {}
        baseline = [
            sample["measurements"][name]["wall_ns_per_call"]
            for sample in samples["baseline"]
        ]
        for mode in modes:
            values = [
                sample["measurements"][name]["wall_ns_per_call"]
                for sample in samples[mode]
            ]
            cpus = [
                sample["measurements"][name]["parent_cpu_ns_per_call"]
                for sample in samples[mode]
            ]
            by_mode[mode] = {
                "median_wall_ns": statistics.median(values),
                "median_parent_cpu_ns": statistics.median(cpus),
                "median_wall_speedup_vs_baseline": statistics.median(baseline)
                / statistics.median(values),
                "paired_wall_speedups": [
                    before / after
                    for before, after in zip(baseline, values, strict=True)
                ],
            }
        summary[name] = by_mode
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "rounds": args.rounds,
        "modes": modes,
        "harness_sha256": harness_hash,
        "consumer_receipts_sha256": receipts_hash,
        "consumer_receipts": json.loads(_RECEIPTS.read_text(encoding="utf-8")),
        "fixture": fixture,
        "measurement": "complete public calls, wall and parent CPU; child CPU excluded; cProfile outside timing",
        "summary": summary,
        "samples": samples,
    }


def main() -> None:
    """Compare saved and current proc implementations on receipt-backed commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--modes", default=",".join(_MODES))
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--mode", choices=_MODES, default="candidate", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.worker:
        if args.fixture is None:
            parser.error("worker needs a fixture manifest")
        print(json.dumps(_worker(args)))
        return
    if args.baseline_source is None or args.output is None or args.rounds < 1:
        parser.error("baseline source, output and positive rounds are required")
    report = _run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, row in report["summary"].items():
        print(
            name
            + ": "
            + ", ".join(
                f"{mode} {value['median_wall_ns'] / 1e6:.3f}ms ({value['median_wall_speedup_vs_baseline']:.3f}x)"
                for mode, value in row.items()
            )
        )


if __name__ == "__main__":
    main()
