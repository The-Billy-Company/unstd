"""Small, killable resource probes with bounded output and measured evidence.

Linux enforces address space in the child; macOS uses a parent RSS watchdog.
Tracemalloc measures Python allocations, while peak RSS includes native heaps.
Neither measurement is a throughput benchmark.
"""

from __future__ import annotations

import importlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
import tracemalloc


if __name__ == "__main__":
    # -I deliberately ignores caller paths and environment Python settings.
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "src"), str(root)]

from tests.support.anchors import REPO_ROOT
from tests.support.process import (
    isolated_environment,
    kill_owned_process_group,
)


MAX_OUTPUT = 65536


def _capture(stream, buffer, overflow):
    with stream:
        while chunk := stream.read(4096):
            remaining = MAX_OUTPUT - len(buffer)
            buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()


def run_resource(
    module,
    function,
    *,
    backend="installed",
    timeout=15,
    cpu_seconds=10,
    memory_mb=512,
    **arguments,
):
    """Run one test-owned probe; crashes, hangs and partial output always fail."""
    if sys.platform not in {"linux", "darwin"}:
        message = "resource probes require Linux or macOS containment"
        raise RuntimeError(message)
    if (
        backend not in {"installed", "stdlib"}
        or not module.startswith("tests.")
        or not function.isidentifier()
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 120
        or type(cpu_seconds) is not int
        or not 1 <= cpu_seconds <= 60
        or type(memory_mb) is not int
        or not 32 <= memory_mb <= 2048
    ):
        message = "invalid resource probe or bounds"
        raise ValueError(message)
    request = json.dumps(
        [module, function, backend, cpu_seconds, memory_mb, arguments], allow_nan=False
    )
    if len(request.encode()) > 4096:
        message = "resource probe arguments exceed 4096 bytes"
        raise ValueError(message)
    command = [sys.executable, "-I", str(Path(__file__).resolve()), request]
    started = time.monotonic()
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter and test-owned code
        command,
        cwd=REPO_ROOT,
        env=isolated_environment(
            OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", UNSTD_CHAOS_OWNED_GROUP="1"
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = bytearray(), bytearray()
    overflow = Event()
    readers = [
        Thread(target=_capture, args=(stream, buffer, overflow), daemon=True)
        for stream, buffer in ((child.stdout, stdout), (child.stderr, stderr))
    ]
    for reader in readers:
        reader.start()
    failure = None
    observed_rss = 0
    try:
        while child.poll() is None:
            if time.monotonic() - started >= timeout:
                failure = "wall deadline exceeded"
                break
            if overflow.is_set():
                failure = "output budget exceeded"
                break
            if sys.platform == "darwin":
                sample = subprocess.run(  # noqa: S603 - OS-owned ps, literal PID
                    ["/bin/ps", "-o", "rss=", "-p", str(child.pid)],
                    capture_output=True,
                    timeout=1,
                    check=False,
                )
                if sample.stdout.strip():
                    observed_rss = max(observed_rss, int(sample.stdout) * 1024)
                    if observed_rss > memory_mb * 1024**2:
                        failure = "RSS budget exceeded"
                        break
            time.sleep(0.02)
    finally:
        # Also reap descendants after a successful parent exit. A child cannot
        # evade ownership by keeping a capture pipe alive after its parent exits.
        try:
            try:
                kill_owned_process_group(child.pid)
            except BaseException as error:
                try:
                    child.kill()
                    child.wait(timeout=5)
                except BaseException as cleanup_error:
                    error.add_note(f"direct-child cleanup failed: {cleanup_error}")
                raise
            else:
                child.wait(timeout=5)
        finally:
            for reader in readers:
                reader.join(timeout=5)
    diagnostic = bytes(stderr).decode("utf-8", "replace")[-4000:]
    assert not failure, (failure, diagnostic)
    assert not overflow.is_set(), "output budget exceeded"
    assert all(not reader.is_alive() for reader in readers), "worker pipes not closed"
    assert child.returncode == 0, (child.returncode, diagnostic)
    assert not stderr, diagnostic
    report = json.loads(stdout)
    assert report["probe"] == f"{module}.{function}"
    assert report["backend"] == backend
    assert isinstance(report["result"], dict)
    assert Path(report["origin"]).is_relative_to(REPO_ROOT / "src/unstd")
    assert report["enforcement"]["cpu"]
    assert report["enforcement"]["address_space"] == (sys.platform == "linux")
    assert report["peak_rss_bytes"] <= memory_mb * 1024**2, "peak RSS budget exceeded"
    if backend == "stdlib":
        assert report["backend_modules"] == []
    report["wall_seconds"] = time.monotonic() - started
    report["observed_rss_bytes"] = observed_rss
    report["enforcement"]["rss_watchdog"] = sys.platform == "darwin"
    report["limits"] = {
        "wall_seconds": timeout,
        "cpu_seconds": cpu_seconds,
        "memory_mb": memory_mb,
        "output_bytes_per_stream": MAX_OUTPUT,
    }
    return report


def _main():
    import resource

    module, function, backend, cpu, memory_mb, arguments = json.loads(sys.argv[1])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    if sys.platform == "linux":
        memory = memory_mb * 1024**2
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    os.environ["UNSTD_CHAOS_BACKEND"] = backend
    from tests.support.chaos import BLOCKED_BACKENDS, apply_backend_policy

    apply_backend_policy()
    probe = getattr(importlib.import_module(module), function)
    import unstd

    tracemalloc.start()
    started = time.process_time()
    result = probe(**arguments)
    current, peak = tracemalloc.get_traced_memory()
    backend_modules = sorted(set(BLOCKED_BACKENDS) & sys.modules.keys())
    versions = {}
    for name in backend_modules:
        try:
            versions[name] = metadata.version("irregex" if name == "irgx" else name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    report = {
        "probe": f"{module}.{function}",
        "backend": backend,
        "origin": str(Path(unstd.__file__).resolve()),
        "python": sys.version,
        "platform": sys.platform,
        "enforcement": {"cpu": True, "address_space": sys.platform == "linux"},
        "arguments": arguments,
        "backend_modules": backend_modules,
        "backend_versions": versions,
        "capabilities": {
            name: {
                key: value
                for key, value in vars(loaded).items()
                if key.startswith("_HAVE_") and type(value) is bool
            }
            for name, loaded in tuple(sys.modules.items())
            if name.startswith("unstd.") and loaded is not None
        },
        "cpu_seconds": time.process_time() - started,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "traced_current_bytes": current,
        "traced_peak_bytes": peak,
        "result": result,
    }
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    _main()
