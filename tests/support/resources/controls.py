"""Small real faults that prove resource workers fail closed and clean up."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import re
import time

from tests.support.chaos.journal import BLOCKED_BACKENDS


def success(*, value: int) -> dict:
    payload = bytearray(256 * 1024)
    payload[-1] = value
    blocked = []
    if os.environ["UNSTD_CHAOS_BACKEND"] == "stdlib":
        for name in BLOCKED_BACKENDS:
            try:
                importlib.import_module(name)
            except ModuleNotFoundError as error:
                if f"stdlib chaos worker blocks {name}" in str(error):
                    blocked.append(name)
            else:
                message = f"fallback imported {name}"
                raise AssertionError(message)
    return {"value": payload[-1], "bytes": len(payload), "blocked": blocked}


def failure(*, kind: str) -> dict:
    if kind == "assertion":
        message = "resource assertion control"
        raise AssertionError(message)
    if kind == "exception":
        message = "resource exception control"
        raise RuntimeError(message)
    if kind == "abort":
        os.abort()
    if kind in {"stdout", "stderr"}:
        descriptor = 1 if kind == "stdout" else 2
        for _ in range(64):
            os.write(descriptor, b"resource output control " * 1024)
        return {}
    message = f"unknown failure control: {kind}"
    raise AssertionError(message)


def hang(*, kind: str, marker: str) -> dict:
    Path(marker).write_text("entered", encoding="utf-8")
    if kind == "gil":
        # CPython's backtracking regex engine holds the GIL throughout this
        # exponential search, defeating watchdogs implemented in child threads.
        re.fullmatch(r"(a+)+", "a" * 40 + "!")
    elif kind == "cpu":
        while True:
            pass
    else:
        time.sleep(60)
    message = "hang control unexpectedly finished"
    raise AssertionError(message)


def memory(*, marker: str, fast: bool = False) -> dict:
    Path(marker).write_text("entered", encoding="utf-8")
    if fast:
        payload = bytearray(160 * 1024**2)
        for offset in range(0, len(payload), 4096):
            payload[offset] = 1
        return {"allocated_bytes": len(payload)}
    blocks = []
    # Touch real pages, while keeping this negative control itself below 192 MiB.
    # Linux rejects the allocation; macOS samples the growing resident set.
    for _ in range(48):
        blocks.append(bytearray(4 * 1024**2))
        time.sleep(0.02)
    time.sleep(60)
    message = "memory control escaped its budget"
    raise AssertionError(message)


def descendant(*, marker: str, finish: bool, shared_helper: bool = False) -> dict:
    if shared_helper:
        from tests.support.environment.anchors import REPO_ROOT
        from tests.support.environment.process import python_worker, worker_line

        code = "import time; print('ready', flush=True); time.sleep(60)"
        with python_worker(code, cwd=REPO_ROOT) as child:
            assert worker_line(child, timeout=2) == "ready\n"
            Path(marker).write_text(str(child.pid), encoding="utf-8")
            if not finish:
                time.sleep(60)
            return {"descendant": child.pid}
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        os.write(write, b"R")
        os.close(write)
        time.sleep(60)
        os._exit(0)
    os.close(write)
    try:
        assert os.read(read, 1) == b"R"
    finally:
        os.close(read)
    Path(marker).write_text(str(pid), encoding="utf-8")
    if not finish:
        time.sleep(60)
    return {"descendant": pid}


def broken_state(*, kind: str) -> dict:
    from tests.support.resources import state as resource_state

    if kind == "clone":
        resource_state.structural._NODES = {}
        resource_state.structural._FIELDS = {}
        return resource_state.clone_cache(size=8)
    if kind == "rex":
        resource_state.rex._MAXCACHE = 2048
        return resource_state.rex_cache(size=1024)
    if kind == "filesystem":
        resource_state.fs._close_scratch_fd = lambda *_args: None
        return resource_state.filesystem(size=4)
    message = f"unknown state control: {kind}"
    raise AssertionError(message)
