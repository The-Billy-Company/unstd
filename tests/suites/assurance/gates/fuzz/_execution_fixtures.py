"""Reviewed subprocess-only controls proving campaign failures remain findings."""

# This worker deliberately has no pytest dependency.
# ruff: noqa: PT017

from __future__ import annotations

import importlib
import mmap
import os
from pathlib import Path
import subprocess
import sys
import time


def target(name):
    if name == "startup":
        msg = "reviewed startup failure"
        raise ImportError(msg)

    def run(data):
        if name == "assertion":
            assert data == b"accepted", "reviewed assertion finding"
        elif name == "unexpected":
            msg = "reviewed unexpected exception"
            raise RuntimeError(msg)
        elif name == "memory":
            msg = "reviewed memory exhaustion"
            raise MemoryError(msg)
        elif name == "address_space":
            # An untouched 2 GiB virtual mapping must fail under the 1 GiB
            # Linux address-space cap. A broken limiter does not commit 2 GiB
            # of physical RAM: the mapping is closed without touching pages.
            with mmap.mmap(-1, 2 * 1024 * 1024 * 1024) as allocated:
                assert len(allocated) == 2 * 1024 * 1024 * 1024
        elif name == "hang":
            time.sleep(120)
        elif name == "abort":
            os.abort()
        elif name == "cpu":
            while True:
                pass
        elif name == "flood":
            sys.stdout.write("synthetic target chatter " * 100000)
            sys.stdout.write("reviewed final diagnostic\n")
            sys.stdout.flush()
        elif name == "blocked":
            # These modules can be installed: the finder must still refuse all
            # optional capability imports before any unstd import occurs.
            for module in ("orjson", "numpy", "ruamel.yaml", "google.protobuf"):
                try:
                    importlib.import_module(module)
                except ModuleNotFoundError as error:
                    assert "stdlib fuzz backend blocks" in str(error)
                else:
                    msg = f"optional backend was not blocked: {module}"
                    raise AssertionError(msg)
        elif name == "descendant":
            # A real descendant inherits the owned process group. Its marker
            # would appear after parent cleanup if the watchdog leaked it.
            process = subprocess.Popen(  # noqa: S603
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import pathlib,sys,time; time.sleep(1); pathlib.Path(sys.argv[1]).touch()",
                    str(Path(data.decode())),
                ]
            )
            assert process.pid > 0
            print("reviewed descendant started", flush=True)
            time.sleep(120)

    return run
