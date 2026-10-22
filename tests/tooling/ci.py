"""Run complete, disjoint CI groups with readable logs and test summaries."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from tests.support.anchors import REPO_ROOT
from tests.tooling.runner import InventoryError, Suite, check_inventory


if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class Group:
    """A stable CI display name and its inventoried suite owners."""

    id: str
    title: str
    suites: tuple[str, ...]


GROUPS = (
    Group("api", "API & regressions", ("unit", "regression")),
    Group(
        "contracts",
        "Compatibility & boundaries",
        (
            "parity",
            "properties",
            "security-contracts",
            "security-structure",
            "integration",
            "pentest",
        ),
    ),
    Group("tooling", "Test tooling & corpus", ("gates", "fuzz-replay")),
    Group("packaging", "Packaging & consumers", ("packaging",)),
)
OPT_IN_SUITES = frozenset({"chaos", "concurrency", "resources", "performance"})
DEFAULT_SELECTION = "not perf and not chaos and not concurrency and not resources"


def check_groups(repo_root: Path) -> dict[str, tuple[Suite, ...]]:
    """Require every active default suite in exactly one explicit CI group."""
    active = {
        suite.id: suite
        for suite in check_inventory(repo_root)
        if suite.status == "active"
    }
    claimed: set[str] = set()
    groups: dict[str, tuple[Suite, ...]] = {}
    for group in GROUPS:
        if group.id in groups or not group.suites:
            message = f"duplicate or empty CI group: {group.id}"
            raise InventoryError(message)
        for identity in group.suites:
            if identity in claimed:
                message = f"suite appears in multiple CI groups: {identity}"
                raise InventoryError(message)
            if identity not in active or identity in OPT_IN_SUITES:
                message = (
                    f"CI group {group.id} requires an active default suite: {identity}"
                )
                raise InventoryError(message)
            claimed.add(identity)
        groups[group.id] = tuple(active[identity] for identity in group.suites)
    missing = active.keys() - OPT_IN_SUITES - claimed
    if missing:
        message = f"active suites have no CI group: {', '.join(sorted(missing))}"
        raise InventoryError(message)
    return groups


@dataclass(frozen=True)
class Report:
    """Aggregate counts from pytest's JUnit suite records."""

    tests: int
    failures: int
    errors: int
    skipped: int
    duration: float


def _report(path: Path) -> Report:
    root = ElementTree.parse(path).getroot()  # noqa: S314 — local pytest output
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if root.tag not in {"testsuite", "testsuites"} or not suites:
        message = "report contains no JUnit test suites"
        raise ValueError(message)
    counts = {
        key: sum(int(suite.attrib[key]) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    duration = sum(float(suite.attrib["time"]) for suite in suites)
    if (
        counts["tests"] <= 0
        or any(value < 0 for value in counts.values())
        or sum(counts[key] for key in ("failures", "errors", "skipped"))
        > counts["tests"]
        or not math.isfinite(duration)
        or duration < 0
    ):
        message = "report contains empty or invalid test totals"
        raise ValueError(message)
    return Report(**counts, duration=duration)


def _summarize(title: str, path: Path, returncode: int) -> int:
    try:
        report = _report(path)
    except (OSError, ElementTree.ParseError, ValueError, KeyError) as exc:
        detail = f"JUnit report unavailable: {exc}"
        print(detail, file=sys.stderr)
        returncode = returncode or 1
    else:
        if report.failures or report.errors:
            returncode = returncode or 1
        passed = report.tests - report.failures - report.errors - report.skipped
        detail = (
            f"{report.tests} tests: {passed} passed, {report.failures} failures, "
            f"{report.errors} errors, {report.skipped} skipped; {report.duration:.2f}s"
        )
    result = "passed" if returncode == 0 else f"failed (exit {returncode})"
    summary = f"**{title}** — {result}. {detail}\n\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with Path(summary_path).open("a", encoding="utf-8") as stream:
                stream.write(summary)
        except OSError as exc:
            print(f"cannot write GitHub test summary: {exc}", file=sys.stderr)
            returncode = returncode or 1
    return returncode


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    """Validate, list, or execute one complete group with literal pytest argv."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate complete, disjoint CI ownership")
    commands.add_parser("list", help="list CI groups and their suite owners")
    run = commands.add_parser("run", help="run one CI group through pytest")
    run.add_argument("group", choices=[group.id for group in GROUPS])
    run.add_argument("pytest_args", nargs=argparse.REMAINDER)
    report_command = commands.add_parser(
        "report", help="summarize an existing JUnit report"
    )
    report_command.add_argument("title")
    report_command.add_argument("path", type=Path)
    report_command.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args(argv)
    root = (repo_root or REPO_ROOT).resolve()
    if args.command == "report":
        return _summarize(args.title, root / args.path, args.exit_code)
    try:
        groups = check_groups(root)
    except InventoryError as exc:
        print(f"CI groups: {exc}", file=sys.stderr)
        return 2
    if args.command == "check":
        print(
            f"CI groups valid: {len(groups)} groups, {sum(map(len, groups.values()))} suites"
        )
        return 0
    if args.command == "list":
        for group in GROUPS:
            print(f"{group.id}\t{group.title}\t{', '.join(group.suites)}")
        return 0
    group = next(group for group in GROUPS if group.id == args.group)
    report = root / ".local/ci" / f"{group.id}.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    extra = args.pytest_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(path for suite in groups[group.id] for path in suite.paths),
        "-m",
        DEFAULT_SELECTION,
        *extra,
        f"--junitxml={report}",
    ]
    folded = os.environ.get("GITHUB_ACTIONS") == "true"
    if folded:
        print(f"::group::{group.title}", flush=True)
    try:
        # Validated owned paths and caller arguments remain literal argv entries.
        returncode = subprocess.run(command, cwd=root, check=False).returncode  # noqa: S603
    except OSError as exc:
        print(f"cannot start pytest: {exc}", file=sys.stderr)
        returncode = 1
    finally:
        if folded:
            print("::endgroup::", flush=True)
    return _summarize(group.title, report, returncode)


if __name__ == "__main__":
    raise SystemExit(main())
