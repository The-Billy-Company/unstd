"""One bounded, isolated comparison of gzip readers and their native inflater.

Every revision runs twice in fixed before/current/current/before order. Raw
wall and thread-CPU samples are diagnostic; they never replace a frozen ceiling.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast
import zlib

from bench import speeds
from bench.workloads.speed_cases_codecs import _compress_cases


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def cases() -> tuple[list[speeds.Case], dict[str, str | int]]:
    """Use the original read fixtures and extract their actual deflate member."""
    from unstd.compress import _gzip_native

    readers = [
        case
        for case in _compress_cases()
        if case.name in {"unstd.compress.gzipx.GzipFile", "unstd.compress.gzipx.open"}
    ]
    packed = inspect.getclosurevars(readers[0].run).nonlocals["packed"]
    raw = gzip.decompress(packed)
    source = io.BytesIO(packed)
    read_header = cast(
        "Callable[[io.BytesIO], int | None]", vars(gzip)["_read_gzip_header"]
    )
    read_header(source)
    deflated = source.read()[: -struct.calcsize("<II")]
    speeds.expect(raw)(zlib.decompress(deflated, -zlib.MAX_WBITS))

    def inflate() -> bytes:
        decoder = _gzip_native.Inflater(wbits=-zlib.MAX_WBITS)
        return decoder.decompress(deflated, sys.maxsize)

    return [
        *readers,
        speeds.Case(
            "unstd.compress._gzip_native.Inflater#read-control",
            inflate,
            speeds.expect(raw),
            "fresh native inflater; exact read fixture deflate bytes; sys.maxsize budget",
        ),
    ], {
        "gzip_sha256": hashlib.sha256(packed).hexdigest(),
        "deflate_sha256": hashlib.sha256(deflated).hexdigest(),
        "decoded_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_bytes": len(raw),
    }


def worker(installation: Path, *, rounds: int, reverse: bool) -> dict[str, Any]:
    """Measure one real installed revision with identical setup before timing."""
    import unstd
    from unstd.compress import _gzip_native

    expected = installation.resolve() / "unstd"
    if Path(unstd.__file__).resolve().parent != expected or not Path(
        _gzip_native.__file__
    ).resolve().is_relative_to(expected):
        message = "gzip control imported the wrong installation"
        raise speeds.RegistryError(message)
    with speeds._pin_cpu():
        selected, payload = cases()
        if reverse:
            selected.reverse()
        environment = speeds.environment()
        provenance = speeds.provenance()
        measured = speeds.timings(selected, rounds=rounds)
        return {
            "environment": environment,
            "provenance": provenance,
            "payload": payload,
            "order": [case.name for case in selected],
            "cases": {name: asdict(result) for name, result in measured.items()},
        }


def compare(
    before: Path, current: Path, *, rounds: int, output: Path | None = None
) -> dict[str, Any]:
    """Run exactly four fresh processes; retain every result without retries."""
    records: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "diagnostic_only": True,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "records": records,
    }

    def retain() -> None:
        if output is not None:
            output.write_text(json.dumps(report, indent=2) + "\n")

    for repetition, order in enumerate((("before", "current"), ("current", "before"))):
        for revision in order:
            installation = before if revision == "before" else current
            command = [
                sys.executable,
                "-m",
                "bench.diagnostics.gzip_reads",
                "worker",
                "--installation",
                str(installation.resolve()),
                "--rounds",
                str(rounds),
            ]
            if repetition:
                command.append("--reverse")
            process = subprocess.run(  # noqa: S603 — this interpreter and fixed module; paths remain argv values
                command,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        (str(installation.resolve()), str(speeds.ROOT))
                    ),
                },
            )
            if process.returncode:
                report["failure"] = {
                    "revision": revision,
                    "repetition": repetition,
                    "returncode": process.returncode,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                }
                retain()
                process.check_returncode()
            result = json.loads(process.stdout)
            mismatch = bool(records) and (
                not speeds.comparable_environment(
                    records[0]["result"]["environment"],
                    result["environment"],
                    revision=True,
                )
                or records[0]["result"]["payload"] != result["payload"]
            )
            records.append(
                {"revision": revision, "repetition": repetition, "result": result}
            )
            retain()
            if mismatch:
                message = "gzip control runtime or payload differs"
                raise speeds.RegistryError(message)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Write focused evidence without modifying any baseline or product file."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="verb", required=True)
    run = commands.add_parser("run")
    run.add_argument("--before-install", type=Path, required=True)
    run.add_argument("--current-install", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    child = commands.add_parser("worker")
    child.add_argument("--installation", type=Path, required=True)
    child.add_argument("--reverse", action="store_true")
    for command in (run, child):
        command.add_argument("--rounds", type=int, default=9, choices=range(3, 16))
    args = parser.parse_args(argv)
    if args.verb == "worker":
        result = worker(args.installation, rounds=args.rounds, reverse=args.reverse)
        print(json.dumps(result))
    else:
        compare(
            args.before_install,
            args.current_install,
            rounds=args.rounds,
            output=args.output,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
