"""Run or replay seed-recorded chaos scenarios with independent hard deadlines."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

from tests.tooling.chaos import execution
from tests.tooling.ci.campaigns import append_campaign_summary, campaign_group
from tests.tooling.suites.runner import check_inventory


def _parser(lane="chaos"):
    parser = argparse.ArgumentParser(
        description=f"Run or replay recorded {lane} scenarios with hard worker deadlines."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    commands.add_parser("check")
    run = commands.add_parser("run")
    run.add_argument("--group", default="all")
    run.add_argument("--seed", type=int, default=173)
    run.add_argument("--rounds", type=int, default=1)
    run.add_argument("--backend", choices=("installed", "stdlib"), default="installed")
    run.add_argument("--wall-seconds", type=float, default=120)
    run.add_argument("--per-test-seconds", type=float, default=45)
    run.add_argument("--cpu-seconds", type=int, default=60)
    run.add_argument("--memory-mb", type=int, default=2048)
    run.add_argument("--artifact-dir", type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("report", type=Path)
    replay.add_argument("--artifact-dir", type=Path)
    return parser


def _read_replay(path, *, lane="chaos"):
    with path.open("rb") as stream:
        raw = stream.read(execution.MAX_REPORT + 1)
    if len(raw) > execution.MAX_REPORT:
        message = "replay report exceeds evidence budget"
        raise ValueError(message)

    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                message = f"duplicate replay field: {name}"
                raise ValueError(message)
            result[name] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique)
    if isinstance(data, dict) and data.get("lane", "chaos") != lane:
        message = "replay belongs to a different campaign lane"
        raise ValueError(message)
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        message = "unsupported chaos replay report"
        raise ValueError(message)
    fields = ("group", "seed", "backend", "limits", "schedule_version")
    if (
        any(name not in data for name in fields)
        or type(data["schedule_version"]) is not int
        or data["schedule_version"] != 1
    ):
        message = "replay requires a versioned group, seed, backend and limits"
        raise ValueError(message)
    if not isinstance(data["limits"], dict) or set(data["limits"]) != {
        "wall_seconds",
        "per_test_seconds",
        "cpu_seconds",
        "memory_mb",
    }:
        message = "invalid replay execution limits"
        raise ValueError(message)
    return data


def _execute(args, groups, *, lane="chaos"):
    if args.command == "replay":
        prior = _read_replay(args.report, lane=lane)
        selected, seed, backend, rounds, limits = (
            [prior["group"]],
            prior["seed"],
            prior["backend"],
            1,
            prior["limits"],
        )
    else:
        selected = list(groups) if args.group == "all" else [args.group]
        seed, backend, rounds = args.seed, args.backend, args.rounds
        limits = {
            name: getattr(args, name)
            for name in (
                "wall_seconds",
                "per_test_seconds",
                "cpu_seconds",
                "memory_mb",
            )
        }
    if (
        type(rounds) is not int
        or not 1 <= rounds <= 100
        or type(seed) is not int
        or not 0 <= seed < 2**64
    ):
        message = "campaign requires 1..100 rounds and an unsigned 64-bit seed"
        raise ValueError(message)
    if any(group not in groups for group in selected) or backend not in {
        "installed",
        "stdlib",
    }:
        message = "unknown chaos group or backend"
        raise ValueError(message)
    for name in ("wall_seconds", "per_test_seconds"):
        value = limits[name]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 < value <= 3600
        ):
            message = f"{name} must be finite and within (0, 3600]"
            raise ValueError(message)
    for name, maximum in (("cpu_seconds", 3600), ("memory_mb", 65536)):
        value = limits[name]
        if type(value) is not int or not 1 <= value <= maximum:
            message = f"{name} must be within 1..{maximum}"
            raise ValueError(message)
    directory = (
        args.artifact_dir
        or execution.REPO
        / f".local/{lane}"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    ).resolve()
    reports = []
    rows = []
    for round_index in range(rounds):
        current_seed = (seed + round_index) % 2**64
        for group in selected:
            output = directory / f"round-{round_index:03d}" / group / backend
            with campaign_group(f"{group} / {backend} / seed {current_seed}"):
                report = execution.run_group(
                    group,
                    seed=current_seed,
                    backend=backend,
                    artifact_dir=output,
                    **({"lane": lane} if lane != "chaos" else {}),
                    **limits,
                )
                print(
                    f"{group}/{backend}/seed-{current_seed}: {report['status']} ({report.get('passed', 0)} passed, {report.get('skipped', 0)} skipped)"
                )
            reports.append(
                {
                    "group": group,
                    "backend": backend,
                    "seed": current_seed,
                    "status": report["status"],
                    "passed": report.get("passed", 0),
                    "skipped": report.get("skipped", 0),
                    "path": str(output / "report.json"),
                }
            )
            rows.append(
                (
                    group,
                    current_seed,
                    report["status"],
                    report.get("passed", 0),
                    report.get("skipped", 0),
                    f"{report.get('duration_seconds', 0):.2f}s",
                    f"`{(output / 'report.json').relative_to(directory)}`",
                )
            )
    status = (
        "passed"
        if reports and all(report["status"] == "passed" for report in reports)
        else "failed"
    )
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "lane": lane,
        "command": args.command,
        "status": status,
        "reports": reports,
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(f"Reports: {directory / 'summary.json'}")
    title = "Fault injection" if lane == "chaos" else "Concurrency"
    append_campaign_summary(
        f"{title} / {backend}: {status}",
        ("Group", "Seed", "Result", "Passed", "Skipped", "Time", "Replay report"),
        rows,
    )
    return 0 if status == "passed" else 1


def main(argv=None, *, lane="chaos"):
    """Preserve all worker failures; unsupported or empty runs cannot pass."""
    args = _parser(lane).parse_args(argv)
    try:
        check_inventory(execution.REPO)
        groups = execution.discover() if lane == "chaos" else execution.discover(lane)
        if args.command == "check":
            print(f"Validated {len(groups)} executable {lane} groups")
            return 0
        if args.command == "list":
            if args.json:
                print(
                    json.dumps(
                        {
                            "include": [
                                {"group": group, "backend": backend, "os": system}
                                for group in groups
                                for backend in ("installed", "stdlib")
                                for system in ("ubuntu-latest", "macos-latest")
                            ]
                        }
                    )
                )
            else:
                for group, path in groups.items():
                    print(f"{group}: {path.relative_to(execution.REPO)}")
            return 0
        return _execute(args, groups, lane=lane)
    except (ValueError, OSError, TypeError) as error:
        print(f"{lane.capitalize()} admission failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
