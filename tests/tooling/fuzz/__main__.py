"""Reviewed corpus replay and bounded parser campaigns from a single inventory."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from tests.tooling.fuzz import execution, mutate, registry


REPO = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List validated CI target identities")
    listing.add_argument("--json", action="store_true", help="Emit a GitHub matrix")
    listing.add_argument(
        "--all-backends",
        action="store_true",
        help="Include every supported target/backend pair",
    )
    commands.add_parser("check", help="Authenticate every target and corpus input")
    for name in ("replay", "campaign", "reproduce"):
        command = commands.add_parser(name)
        command.add_argument("--target", default="all", required=name == "reproduce")
        command.add_argument(
            "--backend", choices=("installed", "stdlib"), default="installed"
        )
        command.add_argument("--artifact-dir", type=Path)
        command.add_argument("--wall-seconds", type=float, default=30)
        command.add_argument("--cpu-seconds", type=int, default=20)
        command.add_argument("--memory-mb", type=int, default=1024)
        command.add_argument("--per-input-seconds", type=float, default=3)
        command.add_argument("--max-input-bytes", type=int, default=65536)
        if name == "campaign":
            command.add_argument(
                "--engine",
                choices=("deterministic", "atheris"),
                default="deterministic",
            )
            command.add_argument("--runs", type=int, default=1000)
            command.add_argument("--seed", type=int, default=1)
        elif name == "reproduce":
            command.add_argument("--input", type=Path, required=True)
    return parser


def _validate(args: argparse.Namespace) -> None:
    for name in ("wall_seconds", "per_input_seconds"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 < value <= 3600:
            message = f"{name} must be finite and within (0, 3600]"
            raise ValueError(message)
    for name, maximum in (
        ("cpu_seconds", 3600),
        ("memory_mb", 65536),
        ("max_input_bytes", 65536),
    ):
        if not 1 <= getattr(args, name) <= maximum:
            message = f"{name} must be within 1..{maximum}"
            raise ValueError(message)
    if args.command == "campaign" and (
        not 1 <= args.runs <= 100000 or not 1 <= args.seed <= 2**32 - 1
    ):
        message = "campaign requires 1..100000 runs and seed in 1..2**32-1"
        raise ValueError(message)
    if args.command == "reproduce" and args.target == "all":
        message = "reproduce requires one explicit target"
        raise ValueError(message)


def _select(targets: tuple[registry.Target, ...], name: str, backend: str):
    selected = [target for target in targets if name == "all" or name == target.name]
    if not selected:
        message = f"unknown fuzz target: {name}"
        raise ValueError(message)
    unsupported = [target.name for target in selected if backend not in target.backends]
    if unsupported and name != "all":
        message = f"{name} does not support backend {backend}"
        raise ValueError(message)
    return [target for target in selected if backend in target.backends], unsupported


def _revision() -> dict:
    def git(*arguments):
        result = subprocess.run(  # noqa: S603 - fixed executable and fixed arguments
            ["git", "-C", str(REPO), *arguments],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    try:
        revision = git("rev-parse", "HEAD")
        status = git("status", "--porcelain", "--untracked-files=normal")
    except (OSError, subprocess.TimeoutExpired):
        return {"revision": None, "dirty": None}
    return {"revision": revision, "dirty": bool(status) if status is not None else None}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _limits(args: argparse.Namespace, target: registry.Target, directory: Path) -> dict:
    return {
        "backend": args.backend,
        "wall_seconds": args.wall_seconds,
        "cpu_seconds": args.cpu_seconds,
        "memory_mb": args.memory_mb,
        "per_input_seconds": args.per_input_seconds,
        "max_input_bytes": min(args.max_input_bytes, target.max_input_bytes),
        "artifact_dir": directory,
    }


def _execute(args: argparse.Namespace, targets: tuple[registry.Target, ...]) -> int:
    _validate(args)
    selected, unsupported = _select(targets, args.target, args.backend)
    directory = args.artifact_dir or REPO / ".local" / "fuzz" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    directory = directory.resolve()
    provenance = {
        "source": _revision(),
        "input_version": 1,
        "mutator_version": mutate.VERSION,
        "tooling_sha256": {
            name: hashlib.sha256((registry.ROOT / name).read_bytes()).hexdigest()
            for name in ("__main__.py", "registry.py", "registry.toml", "mutate.py")
        },
    }
    summary = {
        "schema_version": 1,
        "command": args.command,
        "backend": args.backend,
        "unsupported_targets": unsupported,
        "status": "failed",
        "provenance": provenance,
        "reports": [],
    }
    for target in selected:
        seeds = registry.load_seeds(target)
        stages = ["reproduce"] if args.command == "reproduce" else ["replay"]
        if args.command == "campaign":
            stages.append("campaign")
        for stage in stages:
            output = directory / target.name / args.backend / stage
            limits = _limits(args, target, output)
            if stage == "reproduce":
                with args.input.open("rb") as stream:
                    data = stream.read(limits["max_input_bytes"] + 1)
                case_id = "reproduce/" + hashlib.sha256(data).hexdigest()
                report = execution.run_inputs(target.name, [(case_id, data)], **limits)
            elif stage == "replay":
                report = execution.run_inputs(
                    target.name,
                    [(seed.identity, seed.data) for seed in seeds],
                    **limits,
                )
            elif args.engine == "deterministic":
                report = execution.run_inputs(
                    target.name,
                    mutate.mutations(
                        seeds,
                        runs=args.runs,
                        seed=args.seed,
                        max_input_bytes=limits["max_input_bytes"],
                    ),
                    **limits,
                )
                report.update(
                    engine="deterministic-mutation", runs=args.runs, seed=args.seed
                )
            else:
                # seeds.json is provenance, never a parser input. Only the
                # authenticated bytes enter libFuzzer's disposable directory.
                with tempfile.TemporaryDirectory(
                    prefix="unstd-reviewed-corpus-"
                ) as temporary:
                    raw = Path(temporary)
                    for seed in seeds:
                        (raw / seed.file).write_bytes(seed.data)
                    report = execution.run_atheris(
                        target.name, raw, runs=args.runs, seed=args.seed, **limits
                    )
            report.update(
                stage=stage,
                provenance=provenance,
                reviewed_corpus_sha256=registry.corpus_digest(seeds),
            )
            path = output / "report.json"
            _write(path, report)
            summary["reports"].append(
                {
                    "target": target.name,
                    "stage": stage,
                    "status": report["status"],
                    "completed": report["completed"],
                    "path": str(path),
                }
            )
            print(
                f"{target.name}/{args.backend}/{stage}: {report['status']} ({report['completed']} completed)"
            )
            if report["status"] != "passed":
                break
    summary["status"] = (
        "passed"
        if summary["reports"]
        and all(report["status"] == "passed" for report in summary["reports"])
        else "failed"
    )
    _write(directory / "summary.json", summary)
    print(f"Reports: {directory / 'summary.json'}")
    if unsupported:
        print("Unsupported in this backend: " + ", ".join(unsupported))
    return 0 if summary["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    """Return nonzero for invalid admission, missing engines, or any finding."""
    args = _parser().parse_args(argv)
    try:
        targets = registry.check()
        if args.command == "list":
            if args.json:
                rows = (
                    [
                        {"target": target.name, "backend": backend}
                        for target in targets
                        for backend in target.backends
                    ]
                    if args.all_backends
                    else [{"target": target.name} for target in targets]
                )
                print(json.dumps({"include": rows}))
            else:
                for target in targets:
                    print(
                        f"{target.name}: {', '.join(target.backends)}; {len(registry.load_seeds(target))} seeds"
                    )
            return 0
        if args.command == "check":
            count = sum(len(registry.load_seeds(target)) for target in targets)
            print(f"Validated {len(targets)} targets and {count} reviewed seeds")
            return 0
        return _execute(args, targets)
    except (ValueError, OSError) as error:
        print(f"Fuzz admission failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
