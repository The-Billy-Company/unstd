"""Bounded subprocess replay and genuine Atheris campaigns, without pytest.

The parent imports only the standard library. Every finding retains the exact
active input. Bounded pipe readers keep target output from exhausting parent
memory; a wall watchdog remains effective during startup and native hangs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any

from tests.support.process import kill_owned_process_group


if TYPE_CHECKING:
    from collections.abc import Iterable


HERE = Path(__file__).resolve().parent
_OUTPUT_LIMIT = 32768
_MAX_FRAME = 196608
_TARGETS = frozenset(
    {
        "json",
        "base64",
        "ndjson",
        "structs",
        "tokens",
        "pack",
        "wav",
        "toml",
        "yaml",
        "rex",
        "gzip",
    }
)


def _admit_target(target, backend):
    if target not in _TARGETS or backend not in {"installed", "stdlib"}:
        msg = "target and backend must be reviewed fuzz registry identities"
        raise ValueError(msg)


def _limits(wall_seconds, cpu_seconds, memory_mb, per_input_seconds, max_input_bytes):
    for name, value in (
        ("wall_seconds", wall_seconds),
        ("per_input_seconds", per_input_seconds),
    ):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            msg = f"{name} must be finite and positive"
            raise ValueError(msg)
    for name, value, maximum in (
        ("cpu_seconds", cpu_seconds, 86400),
        ("memory_mb", memory_mb, 1048576),
        ("max_input_bytes", max_input_bytes, 65536),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            msg = f"{name} must be an integer in 1..{maximum}"
            raise ValueError(msg)
    return {
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "memory_mb": memory_mb,
        "per_input_seconds": per_input_seconds,
        "max_input_bytes": max_input_bytes,
    }


class _Child:
    """An owned process group, bounded stderr capture, and framed stdout queue."""

    def __init__(self, command: list[str]):
        environment = os.environ.copy()
        environment.update(
            OPENBLAS_NUM_THREADS="1",
            OMP_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
            NUMEXPR_NUM_THREADS="1",
        )
        self.process = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=os.name == "posix",
        )
        self.messages: queue.Queue = queue.Queue(maxsize=8)
        self.stderr = bytearray()
        self.stderr_bytes = 0
        self.stopped = threading.Event()
        self.readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self.readers:
            reader.start()

    def _put(self, event):
        while not self.stopped.is_set():
            try:
                self.messages.put(event, timeout=0.05)
            except queue.Full:
                continue
            return

    def _read_stdout(self):
        assert self.process.stdout is not None
        try:
            while line := self.process.stdout.readline(_MAX_FRAME + 1):
                if len(line) > _MAX_FRAME or not line.endswith(b"\n"):
                    self._put({"event": "protocol_error"})
                    return
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self._put({"event": "protocol_error"})
                    return
                if not isinstance(event, dict) or not isinstance(
                    event.get("event"), str
                ):
                    self._put({"event": "protocol_error"})
                    return
                self._put(event)
        finally:
            self._put({"event": "eof"})

    def _read_stderr(self):
        assert self.process.stderr is not None
        while data := self.process.stderr.read(8192):
            self.stderr_bytes += len(data)
            self.stderr.extend(data)
            if len(self.stderr) > _OUTPUT_LIMIT:
                # Preserve both startup provenance and final crash evidence.
                del self.stderr[_OUTPUT_LIMIT // 2 : -_OUTPUT_LIMIT // 2]

    def next(self, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"event": "watchdog"}
        try:
            return self.messages.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            return {"event": "tick"}

    def send(self, message):
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps(message, ensure_ascii=True).encode() + b"\n"
        )
        self.process.stdin.flush()

    def _close_pipes(self, error: BaseException | None = None):
        """Finish readers before closing handles they might still be using."""
        self.stopped.set()
        for reader in self.readers:
            reader.join(timeout=1)
        for stream, reader in (
            (self.process.stdin, None),
            (self.process.stdout, self.readers[0]),
            (self.process.stderr, self.readers[1]),
        ):
            if reader is not None and reader.is_alive():
                message = "worker capture pipe did not close"
                if error is None:
                    raise TimeoutError(message)
                error.add_note(message)
                continue
            if stream is not None:
                try:
                    stream.close()
                except BaseException as cleanup_error:
                    if error is None:
                        raise
                    error.add_note(f"worker pipe cleanup failed: {cleanup_error}")

    def close(self):
        """Kill descendants even if the worker exited first, and reap the leader."""
        try:
            if os.name == "posix":
                kill_owned_process_group(self.process.pid)
            elif self.process.poll() is None:
                self.process.kill()
        except BaseException as error:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except BaseException as cleanup_error:
                error.add_note(f"direct-child cleanup failed: {cleanup_error}")
            self._close_pipes(error)
            raise
        else:
            self.process.wait(timeout=5)
            self._close_pipes()


def _failure_for_exit(code: int | None) -> dict:
    if code is not None and code < 0:
        number = -code
        if number == getattr(signal, "SIGXCPU", None):
            kind = "cpu_limit"
        elif number == signal.SIGKILL:
            # SIGKILL has no intrinsic OOM evidence: the kernel or an operator
            # can deliver the same signal. Do not pretend it proves an OOM.
            kind = "killed"
        else:
            kind = "native_crash"
        return {"kind": kind, "signal": number}
    return {"kind": "worker_exit", "returncode": code}


def _atheris_exit(code: int | None, stderr: bytes) -> dict:
    """Recognize libFuzzer's explicit diagnostic evidence, not exit guesses."""
    for diagnostic, kind in (
        (b"ERROR: libFuzzer: timeout after", "input_timeout"),
        (b"ERROR: libFuzzer: out-of-memory", "memory_limit"),
        (b"ERROR: libFuzzer: deadly signal", "native_crash"),
        (b"ERROR: AddressSanitizer:", "sanitizer"),
        (b"WARNING: MemorySanitizer:", "sanitizer"),
    ):
        if diagnostic in stderr:
            return {"kind": kind, "returncode": code, "evidence": diagnostic.decode()}
    return _failure_for_exit(code)


def _report(target, backend, engine, limits):
    digests = {
        str(file.relative_to(HERE)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in (
            HERE / "worker.py",
            HERE / "execution.py",
            HERE / "atheris_driver.py",
            *sorted((HERE / "targets").glob("*.py")),
        )
    }
    return {
        "schema_version": 1,
        "target": target,
        "backend": backend,
        "engine": engine,
        "status": "failed",
        "attempted": 0,
        "completed": 0,
        "limits": limits,
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "process_group_cleanup": os.name == "posix",
        },
        "harness_sha256": digests,
        "production_sha256": {
            str(file.relative_to(HERE.parents[2])): hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
            for file in sorted((HERE.parents[2] / "src" / "unstd").rglob("*.py"))
        },
        "coverage": "Python bytecode only; native wheels are not instrumented"
        if engine == "atheris-libfuzzer"
        else "none; deterministic replay does not collect coverage",
    }


def _write_failure(report, artifact_dir, active):
    if active is not None:
        case_id, data = active
        digest = hashlib.sha256(data).hexdigest()
        report["failure"].update(
            case_id=case_id,
            input_sha256=digest,
            input_bytes=len(data),
        )
        if artifact_dir is None:
            report["failure"]["input_base64"] = base64.b64encode(data).decode("ascii")
    if artifact_dir is None:
        return
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if active is not None:
        path = directory / f"{report['target']}-{digest}.bin"
        path.write_bytes(data)
        report["failure"]["input_path"] = str(path.resolve())
    report_path = directory / f"{report['target']}-{report['backend']}-failure.json"
    report["failure_report"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _command(target, backend, limits, *, driver="worker.py", fixture=None):
    command = [
        sys.executable,
        "-I",
        str(HERE / driver),
        "--target",
        target,
        "--backend",
        backend,
        "--cpu-seconds",
        str(limits["cpu_seconds"]),
        "--memory-mb",
        str(limits["memory_mb"]),
        "--max-input-bytes",
        str(limits["max_input_bytes"]),
    ]
    if fixture is not None:
        command.extend(("--fixture", fixture))
    return command


def run_inputs(
    target_name: str,
    cases: Iterable[tuple[str, bytes]],
    *,
    backend: str = "installed",
    wall_seconds: float = 20,
    cpu_seconds: int = 10,
    memory_mb: int = 1024,
    per_input_seconds: float = 3,
    max_input_bytes: int = 65536,
    artifact_dir: Path | None = None,
    _fixture: str | None = None,
) -> dict[str, Any]:
    """Replay exact ordered inputs in one fresh bounded interpreter per target.

    Empty or oversized corpora fail admission. ``_fixture`` admits only the
    reviewed in-tree execution gates and is never exposed by the public CLI.
    """
    _admit_target(target_name, backend)
    limits = _limits(
        wall_seconds, cpu_seconds, memory_mb, per_input_seconds, max_input_bytes
    )
    report = _report(target_name, backend, "deterministic-replay", limits)
    admitted = []
    ids = set()
    total_bytes = 0
    digest = hashlib.sha256()
    for case_id, data in cases:
        if not isinstance(case_id, str) or not case_id or len(case_id) > 512:
            msg = "case ids must contain 1..512 characters"
            raise ValueError(msg)
        if type(data) is not bytes or len(data) > max_input_bytes:
            msg = "replay input is not bytes or exceeds max_input_bytes"
            raise ValueError(msg)
        if case_id in ids:
            msg = f"duplicate replay case id: {case_id}"
            raise ValueError(msg)
        ids.add(case_id)
        total_bytes += len(data)
        if len(admitted) >= 100000 or total_bytes > 64 * 1024 * 1024:
            msg = "replay corpus exceeds 100000 inputs or 64 MiB admission budget"
            raise ValueError(msg)
        admitted.append((case_id, data))
        encoded_id = case_id.encode("utf-8", "surrogatepass")
        digest.update(len(encoded_id).to_bytes(4, "big"))
        digest.update(encoded_id)
        digest.update(len(data).to_bytes(4, "big"))
        digest.update(data)
    if not admitted:
        msg = "a replay corpus must contain at least one input"
        raise ValueError(msg)
    report["corpus_sha256"] = digest.hexdigest()
    report["cases"] = len(admitted)
    command = _command(target_name, backend, limits, fixture=_fixture)
    started = time.monotonic()
    deadline = started + wall_seconds
    child = _Child(command)
    active = None
    active_deadline = None
    active_started = False
    ready = stopping = complete = False
    index = 0
    try:
        while True:
            event = child.next(min(deadline, active_deadline or deadline))
            kind = event["event"]
            if kind == "tick":
                continue
            if kind == "watchdog":
                report["failure"] = {
                    "kind": "input_timeout"
                    if active_deadline is not None and active_deadline < deadline
                    else "wall_timeout"
                }
                break
            if kind == "ready" and not ready:
                ready = True
                report.update(
                    enforcement=event["enforcement"],
                    backend_modules=event["backend_modules"],
                )
            elif (
                kind == "start"
                and active is not None
                and not active_started
                and event.get("id") == active[0]
            ):
                active_started = True
                report["attempted"] += 1
            elif (
                kind == "done"
                and active is not None
                and active_started
                and event.get("id") == active[0]
            ):
                report["completed"] += 1
                active = active_deadline = None
                active_started = False
                index += 1
            elif (
                kind == "complete" and stopping and report["completed"] == len(admitted)
            ):
                complete = True
            elif kind == "failure":
                report["failure"] = {
                    key: value for key, value in event.items() if key != "event"
                }
                break
            elif kind == "eof":
                code = child.process.wait(
                    timeout=max(0.01, deadline - time.monotonic())
                )
                if (
                    ready
                    and complete
                    and report["attempted"] == len(admitted)
                    and code == 0
                ):
                    report["status"] = "passed"
                else:
                    report["failure"] = _failure_for_exit(code)
                break
            else:
                report["failure"] = {"kind": "protocol_error", "event": kind}
                break
            if ready and active is None and not stopping:
                if index == len(admitted):
                    child.send({"stop": True})
                    stopping = True
                else:
                    active = admitted[index]
                    active_deadline = time.monotonic() + per_input_seconds
                    child.send(
                        {
                            "id": active[0],
                            "data": base64.b64encode(active[1]).decode("ascii"),
                        }
                    )
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as error:
        report["failure"] = {
            "kind": "transport_error",
            "exception": type(error).__name__,
        }
    finally:
        child.close()
        report.update(
            duration_seconds=round(time.monotonic() - started, 6),
            worker_returncode=child.process.returncode,
            stderr=child.stderr.decode("utf-8", "replace"),
            stderr_bytes=child.stderr_bytes,
            stderr_truncated=child.stderr_bytes > _OUTPUT_LIMIT,
        )
    if report["status"] != "passed":
        _write_failure(report, artifact_dir, active)
    return report


def run_atheris(
    target_name: str,
    corpus_dir: Path,
    *,
    backend: str = "installed",
    runs: int = 1000,
    seed: int = 1,
    wall_seconds: float = 20,
    cpu_seconds: int = 10,
    memory_mb: int = 1024,
    per_input_seconds: float = 3,
    max_input_bytes: int = 65536,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    """Run real libFuzzer/Atheris against a disposable copy of reviewed seeds."""
    _admit_target(target_name, backend)
    limits = _limits(
        wall_seconds, cpu_seconds, memory_mb, per_input_seconds, max_input_bytes
    )
    if (
        type(runs) is not int
        or runs <= 0
        or type(seed) is not int
        or not 1 <= seed <= 2**32 - 1
    ):
        msg = "runs must be positive and seed must be in 1..2**32-1"
        raise ValueError(msg)
    source = Path(corpus_dir)
    corpus = sorted(path for path in source.iterdir() if path.is_file())
    if not corpus or len(corpus) > 100000:
        msg = "Atheris requires a nonempty reviewed seed corpus"
        raise ValueError(msg)
    report = _report(target_name, backend, "atheris-libfuzzer", limits)
    report.update(runs=runs, seed=seed)
    corpus_hash = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="unstd-fuzz-") as temporary:
        temporary_path = Path(temporary)
        working = temporary_path / "corpus"
        working.mkdir()
        total_bytes = 0
        for index, path in enumerate(corpus):
            with path.open("rb") as stream:
                raw = stream.read(max_input_bytes + 1)
            if len(raw) > max_input_bytes:
                msg = f"seed exceeds max_input_bytes: {path.name}"
                raise ValueError(msg)
            total_bytes += len(raw)
            if total_bytes > 64 * 1024 * 1024:
                msg = "seed corpus exceeds 64 MiB admission budget"
                raise ValueError(msg)
            corpus_hash.update(len(raw).to_bytes(4, "big"))
            corpus_hash.update(raw)
            (working / f"seed-{index:05d}").write_bytes(raw)
        report["corpus_sha256"] = corpus_hash.hexdigest()
        command = _command(target_name, backend, limits, driver="atheris_driver.py")
        command.extend(
            (
                "--corpus",
                str(working),
                "--runs",
                str(runs),
                "--seed",
                str(seed),
                "--wall-seconds",
                str(wall_seconds),
                "--per-input-seconds",
                str(per_input_seconds),
                "--artifact-prefix",
                str(temporary_path) + os.sep,
            )
        )
        started = time.monotonic()
        deadline = started + wall_seconds
        child = _Child(command)
        active = active_deadline = None
        ready = False
        try:
            while True:
                event = child.next(min(deadline, active_deadline or deadline))
                kind = event["event"]
                if kind == "tick":
                    continue
                if kind == "watchdog":
                    report["failure"] = {
                        "kind": "input_timeout"
                        if active_deadline and active_deadline < deadline
                        else "wall_timeout"
                    }
                    break
                if kind == "ready" and not ready:
                    ready = True
                    report.update(
                        enforcement=event["enforcement"],
                        backend_modules=event["backend_modules"],
                        engine_version=event["engine_version"],
                        engine_flags=event["engine_flags"],
                    )
                elif kind == "start" and ready and active is None:
                    data = base64.b64decode(event["data"], validate=True)
                    if len(data) > max_input_bytes:
                        report["failure"] = {"kind": "protocol_error"}
                        break
                    active = (event["id"], data)
                    active_deadline = time.monotonic() + per_input_seconds
                    report["attempted"] += 1
                elif (
                    kind == "done"
                    and active is not None
                    and event.get("id") == active[0]
                ):
                    active = active_deadline = None
                    report["completed"] += 1
                elif kind == "failure":
                    report["failure"] = {
                        key: value for key, value in event.items() if key != "event"
                    }
                    break
                elif kind == "eof":
                    code = child.process.wait(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                    if (
                        ready
                        and report["completed"] > 0
                        and active is None
                        and code == 0
                    ):
                        report["status"] = "passed"
                    else:
                        report["failure"] = _atheris_exit(code, bytes(child.stderr))
                    break
                else:
                    report["failure"] = {"kind": "protocol_error", "event": kind}
                    break
        except (
            BrokenPipeError,
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
        ) as error:
            report["failure"] = {
                "kind": "transport_error",
                "exception": type(error).__name__,
            }
        finally:
            child.close()
            report.update(
                duration_seconds=round(time.monotonic() - started, 6),
                worker_returncode=child.process.returncode,
                stderr=child.stderr.decode("utf-8", "replace"),
                stderr_bytes=child.stderr_bytes,
                stderr_truncated=child.stderr_bytes > _OUTPUT_LIMIT,
            )
        if report.get("failure", {}).get("kind") in {
            "worker_exit",
            "native_crash",
            "killed",
        }:
            report["failure"] = _atheris_exit(
                child.process.returncode, bytes(child.stderr)
            )
        report["generated_corpus_inputs"] = max(
            0, len(list(working.iterdir())) - len(corpus)
        )
        if artifact_dir is not None:
            destination = Path(artifact_dir) / "coverage-corpus" / target_name
            destination.mkdir(parents=True, exist_ok=True)
            for path in working.iterdir():
                if not path.name.startswith("seed-"):
                    raw = path.read_bytes()
                    (destination / hashlib.sha256(raw).hexdigest()).write_bytes(raw)
            report["coverage_corpus_path"] = str(destination.resolve())
        if report["status"] != "passed":
            _write_failure(report, artifact_dir, active)
    return report
