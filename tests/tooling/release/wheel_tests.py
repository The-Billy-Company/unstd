"""Run the complete installed-wheel contracts in bounded parallel processes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


MODULES = (
    "serde",
    "clone",
    "crypto",
    "text",
    "compress",
    "containers",
    "proc",
    "fs",
    "rand",
    "pack",
    "math",
)


def worker_count() -> int:
    """Use at most four of the CPUs available to this interpreter."""
    available = getattr(os, "process_cpu_count", os.cpu_count)()
    return min(4, max(1, available or 1))


def run_tests(project: Path, *, workers: int) -> int:
    """Run every partition and retain every failure without changing the cwd."""
    if not 1 <= workers <= 4:
        message = "wheel tests require between one and four workers"
        raise ValueError(message)
    paths = [str(project.resolve() / "tests/suites/product" / name) for name in MODULES]
    processes: list[tuple[int, subprocess.Popen[bytes] | None, Path]] = []
    with tempfile.TemporaryDirectory(prefix="unstd-wheel-tests-") as temporary:
        root = Path(temporary)
        try:
            for index in range(1, workers + 1):
                output = root / f"shard-{index}.log"
                command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    *paths,
                    "-p",
                    "tests.tooling.ci.sharding",
                    "--ci-shard",
                    f"{index}/{workers}",
                    "--basetemp",
                    str(root / f"temporary-{index}"),
                    "-q",
                ]
                try:
                    # Files cannot fill a pipe while another shard is being joined.
                    with output.open("wb") as stream:
                        process = subprocess.Popen(  # noqa: S603 — fixed interpreter and repository test paths
                            command, stdout=stream, stderr=subprocess.STDOUT
                        )
                except OSError as error:
                    output.write_text(
                        f"Cannot start shard: {error}\n", encoding="utf-8"
                    )
                    process = None
                processes.append((index, process, output))
            result = 0
            for index, process, output in processes:
                code = process.wait() if process is not None else 2
                print(f"Wheel tests shard {index}/{workers} (exit {code})", flush=True)
                # Redirected Windows streams can use a legacy encoding. Replay
                # the original bytes without decoding or changing the child env.
                with output.open("rb") as stream:
                    shutil.copyfileobj(stream, sys.stdout.buffer)
                sys.stdout.buffer.flush()
                print(flush=True)
                if code and not result:
                    result = max(1, code)
            return result
        finally:
            # An interrupted parent must release workers before removing their files.
            for _, process, _ in processes:
                if process is not None and process.poll() is None:
                    process.terminate()
            for _, process, _ in processes:
                if process is not None and process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def main(argv: Sequence[str] | None = None) -> int:
    """Run from the source distribution while importing the installed wheel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args(argv)
    return run_tests(
        Path(__file__).resolve().parents[3], workers=min(args.workers, worker_count())
    )


if __name__ == "__main__":
    raise SystemExit(main())
