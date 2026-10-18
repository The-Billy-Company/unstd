"""Real instrumented Atheris entry point, launched only in a bounded child."""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
from importlib import metadata
import math
from pathlib import Path
import sys
import time
import traceback


sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker import REQUIRED, configure, emit, load_target


def main() -> None:
    """Instrument imports before loading any target or production module."""
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=sorted(REQUIRED))
    parser.add_argument("--backend", choices=("installed", "stdlib"), required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--max-input-bytes", type=int, required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--runs", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--wall-seconds", type=float, required=True)
    parser.add_argument("--per-input-seconds", type=float, required=True)
    parser.add_argument("--artifact-prefix", required=True)
    args = parser.parse_args()
    try:
        enforcement = configure(args.backend, args.cpu_seconds, args.memory_mb)
        import atheris

        with (
            contextlib.redirect_stdout(sys.stderr),
            atheris.instrument_imports(include=("unstd", "tests")),
        ):
            fuzz, versions = load_target(args.target, args.backend)
        # libFuzzer's clock starts after imports, whereas the parent wall and
        # OS CPU budgets include startup. Reserve cleanup headroom in both so
        # ordinary bounded campaign completion precedes the hard watchdogs.
        fuzz_seconds = max(
            1,
            math.floor(
                min(
                    args.wall_seconds - (time.monotonic() - started) - 2,
                    args.cpu_seconds - time.process_time() - 2,
                )
            ),
        )
        flags = [
            "unstd-fuzz",
            args.corpus,
            f"-runs={args.runs}",
            f"-seed={args.seed}",
            f"-max_total_time={fuzz_seconds}",
            f"-max_len={args.max_input_bytes}",
            f"-timeout={max(1, math.ceil(args.per_input_seconds))}",
            f"-rss_limit_mb={args.memory_mb}",
            f"-artifact_prefix={args.artifact_prefix}",
            "-print_final_stats=1",
        ]
        sequence = 0

        def test_one_input(data: bytes) -> None:
            nonlocal sequence
            sequence += 1
            case_id = str(sequence)
            if len(data) > args.max_input_bytes:
                msg = "Atheris supplied an over-budget input"
                raise AssertionError(msg)  # noqa: TRY301 - fatal engine admission gate
            emit("start", id=case_id, data=base64.b64encode(data).decode("ascii"))
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    fuzz(data)
            except BaseException as error:
                emit(
                    "failure",
                    kind="assertion"
                    if isinstance(error, AssertionError)
                    else "memory_error"
                    if isinstance(error, MemoryError)
                    or (isinstance(error, OSError) and error.errno == errno.ENOMEM)
                    else "unexpected_exception",
                    exception=type(error).__name__,
                    message=str(error)[:2048],
                    traceback=traceback.format_exc(limit=12)[-8192:],
                )
                raise
            emit("done", id=case_id)

        # The callback calls imported instrumented code. libFuzzer must retain
        # native signal handlers so aborts/segfaults remain engine findings.
        atheris.Setup(flags, test_one_input)
        emit(
            "ready",
            enforcement=enforcement,
            backend_modules=versions,
            engine_version=metadata.version("atheris"),
            engine_flags=flags[2:],
        )
        atheris.Fuzz()
    except BaseException as error:
        emit(
            "failure",
            kind="startup_error",
            exception=type(error).__name__,
            message=str(error)[:2048],
            traceback=traceback.format_exc(limit=12)[-8192:],
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
