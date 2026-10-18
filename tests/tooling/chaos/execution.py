"""Bounded campaign ownership, source attribution and explicit result accounting."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

from tests.support.chaos import MAX_EVENTS, MAX_JOURNAL_BYTES, VERSION


REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "tests/suites/assurance/adversarial/chaos"
MAX_REPORT = 16 * 1024 * 1024
EVIDENCE_FILES = (
    "admission.json",
    "active.json",
    "collection.json",
    "cases.jsonl",
    "complete.json",
)


def discover():
    """Derive every executable group from the owning lane; reject empty drift."""
    groups = {}
    for path in sorted(CASES.glob("test_*.py")):
        if path.is_symlink() or not re.fullmatch(
            r"test_[a-z][a-z0-9_]*\.py", path.name
        ):
            message = f"unreviewed chaos group source: {path}"
            raise ValueError(message)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in tree.body
        ):
            message = f"chaos group contains no test functions: {path.name}"
            raise ValueError(message)
        groups[path.stem.removeprefix("test_")] = path
    if not groups:
        message = "chaos lane must contain executable scenarios"
        raise ValueError(message)
    return groups


class _Capture:
    def __init__(self, stream):
        self.stream = stream
        self.buffer = bytearray()
        self.total = 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while value := self.stream.read(8192):
            self.total += len(value)
            self.buffer.extend(value)
            if len(self.buffer) > 32768:
                del self.buffer[16384:-16384]

    def finish(self):
        self.thread.join(timeout=2)
        self.stream.close()
        return {
            "text": self.buffer.decode("utf-8", "replace"),
            "bytes": self.total,
            "truncated": self.total > 32768,
        }


def _read(path, *, lines=False):
    with path.open("rb") as stream:
        data = stream.read(MAX_REPORT + 1)
    if len(data) > MAX_REPORT:
        message = "worker evidence exceeds admission budget"
        raise ValueError(message)
    return (
        [json.loads(line) for line in data.splitlines()] if lines else json.loads(data)
    )


def _classify(returncode):
    if returncode is not None and returncode < 0:
        number = -returncode
        return {
            "kind": "cpu_limit"
            if number == getattr(signal, "SIGXCPU", None)
            else "killed"
            if number == signal.SIGKILL
            else "native_crash",
            "signal": number,
        }
    return {"kind": "worker_exit", "returncode": returncode}


def _valid_journal(journal, *, nodeid, seed):
    if (
        not isinstance(journal, dict)
        or set(journal) != {"version", "seed", "scenario", "events"}
        or type(journal["version"]) is not int
        or journal["version"] != VERSION
        or type(journal["seed"]) is not int
        or journal["seed"] != seed
        or journal["scenario"] != nodeid
        or not isinstance(journal["events"], list)
        or not 1 <= len(journal["events"]) <= MAX_EVENTS
    ):
        return False
    for event in journal["events"]:
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("event"), str)
            or not 1 <= len(event["event"]) <= 128
        ):
            return False
    try:
        return (
            len(json.dumps(journal["events"], allow_nan=False).encode())
            <= MAX_JOURNAL_BYTES
        )
    except (TypeError, ValueError):
        return False


def _valid_accounting(cases, collection, collected):
    if (
        type(collected) is not int
        or collected < 1
        or not isinstance(collection, dict)
        or collection.get("count") != collected
        or not isinstance(collection.get("nodeids"), list)
        or any(
            not isinstance(nodeid, str) or not nodeid
            for nodeid in collection["nodeids"]
        )
        or len(collection["nodeids"]) != collected
        or len(set(collection["nodeids"])) != collected
    ):
        return False
    observed = {nodeid: {} for nodeid in collection["nodeids"]}
    for case in cases:
        if not isinstance(case, dict):
            return False
        nodeid, phase = case.get("nodeid"), case.get("phase")
        if (
            nodeid not in observed
            or phase not in {"setup", "call", "teardown"}
            or phase in observed[nodeid]
            or case.get("outcome") not in {"passed", "failed", "skipped"}
        ):
            return False
        observed[nodeid][phase] = case["outcome"]
    for phases in observed.values():
        if not {"setup", "teardown"} <= set(phases):
            return False
        if (phases["setup"] == "passed") != ("call" in phases):
            return False
    return True


def _live_journals(directory, *, seed):
    result = {}
    total = 0
    for path in sorted(directory.glob("*.json")):
        total += path.stat().st_size
        if (
            path.is_symlink()
            or total > MAX_REPORT
            or path.stat().st_size > 2 * MAX_JOURNAL_BYTES
        ):
            message = "live journal evidence exceeds admission budget"
            raise ValueError(message)
        snapshot = _read(path)
        nodeid = snapshot.get("scenario") if isinstance(snapshot, dict) else None
        if (
            not isinstance(nodeid, str)
            or path.stem != hashlib.sha256(nodeid.encode()).hexdigest()
            or not _valid_journal(snapshot, nodeid=nodeid, seed=seed)
            or nodeid in result
        ):
            message = "invalid live journal identity, schema or replay seed"
            raise ValueError(message)
        result[nodeid] = snapshot
    return result


def run_group(
    group,
    *,
    seed=173,
    backend="installed",
    wall_seconds=120,
    per_test_seconds=45,
    cpu_seconds=60,
    memory_mb=2048,
    artifact_dir=None,
    _fixture=None,
):
    """Run a registered scenario group under an independent parent watchdog."""
    groups = discover()
    if group not in groups or backend not in {"installed", "stdlib"}:
        message = "unknown chaos group or backend"
        raise ValueError(message)
    if type(seed) is not int or not 0 <= seed < 2**64:
        message = "chaos seed must be an unsigned 64-bit integer"
        raise ValueError(message)
    for name, value in (
        ("wall_seconds", wall_seconds),
        ("per_test_seconds", per_test_seconds),
    ):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 < value <= 3600
        ):
            message = f"{name} must be finite and within (0, 3600]"
            raise ValueError(message)
    for name, value, maximum in (
        ("cpu_seconds", cpu_seconds, 3600),
        ("memory_mb", memory_mb, 65536),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            message = f"{name} must be within 1..{maximum}"
            raise ValueError(message)
    if _fixture is not None and _fixture not in {
        "pass",
        "assertion",
        "hang",
        "abort",
        "flood",
        "descendant",
        "skip",
        "empty",
        "no-journal",
        "invalid-journal",
        "cpu",
        "memory",
    }:
        message = "unreviewed campaign fixture"
        raise ValueError(message)
    source_files = [
        *sorted((REPO / "src/unstd").rglob("*.py")),
        *sorted((REPO / "tests/support").glob("*.py")),
        *sorted(CASES.glob("*.py")),
        *sorted(Path(__file__).parent.glob("*.py")),
        REPO / "tests/conftest.py",
        REPO / "tests/tooling/inventory.toml",
    ]
    report = {
        "schema_version": 1,
        "group": group,
        "seed": seed,
        "backend": backend,
        "schedule_version": 1,
        "status": "failed",
        "limits": {
            "wall_seconds": wall_seconds,
            "per_test_seconds": per_test_seconds,
            "cpu_seconds": cpu_seconds,
            "memory_mb": memory_mb,
        },
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "process_group_cleanup": os.name == "posix",
        },
        "source_sha256": {
            str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_files
        },
    }
    destination = Path(artifact_dir) if artifact_dir is not None else None
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
        # A repeated run must not retain the preceding run's completion record.
        for name in (*EVIDENCE_FILES, "report.json"):
            (destination / name).unlink(missing_ok=True)
        if (destination / "journals").is_dir():
            for path in (destination / "journals").glob("*.json"):
                path.unlink()
    with tempfile.TemporaryDirectory(prefix="unstd-chaos-") as temporary:
        directory = Path(temporary)
        selected = str(groups[group].relative_to(REPO))
        if _fixture is not None:
            selected = "tests/suites/assurance/gates/chaos/_campaign_fixtures.py"
        command = [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("worker.py")),
            "--path",
            selected,
            "--seed",
            str(seed),
            "--backend",
            backend,
            "--cpu-seconds",
            str(cpu_seconds),
            "--memory-mb",
            str(memory_mb),
            "--directory",
            str(directory),
        ]
        environment = os.environ.copy()
        if _fixture is not None:
            environment["UNSTD_CHAOS_GATE_FIXTURE"] = _fixture
        started = time.monotonic()
        child = subprocess.Popen(  # noqa: S603 - fixed interpreter and reviewed paths
            command,
            cwd=REPO,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        stdout, stderr = _Capture(child.stdout), _Capture(child.stderr)
        active = None
        active_deadline = None
        try:
            while child.poll() is None:
                now = time.monotonic()
                if now >= started + wall_seconds:
                    report["failure"] = {"kind": "wall_timeout"}
                    break
                active_path = directory / "active.json"
                if active_path.is_file():
                    current = _read(active_path)
                    if active is None or current.get("sequence") != active.get(
                        "sequence"
                    ):
                        active_deadline = (
                            now + per_test_seconds if current.get("nodeid") else None
                        )
                    if not current.get("nodeid"):
                        active_deadline = None
                    active = current
                if active_deadline is not None and now >= active_deadline:
                    report["failure"] = {
                        "kind": "test_timeout",
                        "nodeid": active["nodeid"],
                    }
                    break
                time.sleep(0.02)
        finally:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            elif child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            report.update(
                duration_seconds=round(time.monotonic() - started, 6),
                worker_returncode=child.returncode,
                stdout=stdout.finish(),
                stderr=stderr.finish(),
            )
        try:
            admission = _read(directory / "admission.json")
            report["admission"] = admission
            report["enforcement"] = admission["enforcement"]
            report["backend_modules"] = admission["backend_modules"]
            report["backend_versions"] = admission["backend_versions"]
            report["facade_bindings"] = admission["facade_bindings"]
            if (
                type(admission["schema_version"]) is not int
                or admission["schema_version"] != 1
                or type(admission["seed"]) is not int
                or admission["seed"] != seed
                or admission["backend"] != backend
                or set(admission["enforcement"])
                != {"cpu", "address_space", "core_dumps_disabled", "process_group"}
                or not all(
                    type(value) is bool for value in admission["enforcement"].values()
                )
            ):
                report["admission_error"] = "invalid_schema"
        except (OSError, ValueError, KeyError, TypeError) as error:
            report["admission_error"] = type(error).__name__
        live = {}
        try:
            live = _live_journals(directory / "journals", seed=seed)
            report["live_journals"] = live
            report["journals"] = live
        except (OSError, ValueError, KeyError, TypeError) as error:
            report["live_journal_error"] = type(error).__name__
        try:
            if (directory / "active.json").is_file():
                active = _read(directory / "active.json")
            report["active"] = active
            cases = _read(directory / "cases.jsonl", lines=True)
            report["cases"] = cases
            calls = [case for case in cases if case["phase"] == "call"]
            passed = [case for case in calls if case["outcome"] == "passed"]
            failed = [case for case in cases if case["outcome"] == "failed"]
            skipped = [case for case in cases if case["outcome"] == "skipped"]
            journals = {
                case["nodeid"]: case["properties"]["chaos_journal"]
                for case in cases
                if "chaos_journal" in case["properties"]
            }
            journals.update(live)
            report.update(
                passed=len(passed),
                skipped=len(skipped),
                failed=len(failed),
                journals=journals,
            )
            complete = _read(directory / "complete.json")
            collection = _read(directory / "collection.json")
            report["collection"] = collection
            report.update(
                enforcement=complete["enforcement"],
                collected=complete["collected"],
                backend_modules=complete["backend_modules"],
                backend_versions=complete["backend_versions"],
                facade_bindings=complete["facade_bindings"],
            )
            if "failure" not in report:
                if "admission_error" in report:
                    report["failure"] = {"kind": "admission_evidence"}
                elif "live_journal_error" in report or not set(live) <= set(
                    collection["nodeids"]
                ):
                    report["failure"] = {"kind": "invalid_journal"}
                elif backend == "stdlib" and complete["backend_modules"]:
                    report["failure"] = {"kind": "backend_leak"}
                elif failed:
                    report["failure"] = {
                        "kind": "assertion",
                        "nodeids": [case["nodeid"] for case in failed],
                    }
                elif child.returncode != 0 or complete["exitcode"] != 0:
                    report["failure"] = _classify(child.returncode)
                elif (
                    not passed
                    or not _valid_accounting(cases, collection, complete["collected"])
                    or len(calls)
                    + len([case for case in skipped if case["phase"] == "setup"])
                    != complete["collected"]
                ):
                    report["failure"] = {"kind": "incomplete_or_empty"}
                elif any(
                    not isinstance(journals.get(case["nodeid"]), dict)
                    or not journals[case["nodeid"]].get("events")
                    for case in passed
                ):
                    report["failure"] = {"kind": "missing_journal"}
                elif any(
                    not _valid_journal(
                        journals[case["nodeid"]], nodeid=case["nodeid"], seed=seed
                    )
                    for case in passed
                ):
                    report["failure"] = {"kind": "invalid_journal"}
                else:
                    report["status"] = "passed"
        except (OSError, ValueError, KeyError, TypeError) as error:
            report.setdefault("failure", _classify(child.returncode))
            report["evidence_error"] = type(error).__name__
        if destination is not None:
            for name in EVIDENCE_FILES:
                path = directory / name
                if path.is_file():
                    with path.open("rb") as stream:
                        (destination / name).write_bytes(stream.read(MAX_REPORT))
            if live:
                journal_directory = destination / "journals"
                journal_directory.mkdir(exist_ok=True)
                for nodeid, snapshot in live.items():
                    name = hashlib.sha256(nodeid.encode()).hexdigest() + ".json"
                    (journal_directory / name).write_text(
                        json.dumps(snapshot, allow_nan=False)
                    )
            (destination / "report.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )
    return report
