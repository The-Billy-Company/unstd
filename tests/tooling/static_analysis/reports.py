"""Fail closed on incomplete, unsuccessful, or non-clean analysis evidence.

Scanners own analysis and SARIF generation. This gate makes their saved results
mandatory even when a scanner exits zero after reporting findings. Suppression
and baseline metadata never exempt a result from the repository's clean gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, NoReturn


if TYPE_CHECKING:
    from collections.abc import Sequence


class StaticAnalysisError(ValueError):
    """Analysis evidence is incomplete, unsuccessful, or contains a finding."""


def _fail(message: str) -> NoReturn:
    raise StaticAnalysisError(message)


def _object(value: object, context: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{context}: expected an object")
    return value


def _list(value: object, context: str) -> list:
    if not isinstance(value, list):
        _fail(f"{context}: expected a list")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    _fail(f"invalid JSON constant {value}")


def _notifications(value: object, context: str) -> None:
    for index, item in enumerate(_list(value, context)):
        notification = _object(item, f"{context}[{index}]")
        level = notification.get("level", "warning")
        if level not in ("none", "note", "warning", "error"):
            _fail(f"{context}[{index}]: invalid notification level")
        if level == "error":
            _fail(f"{context}[{index}]: scanner reported an error")


def _invocations(value: object, context: str) -> None:
    for index, item in enumerate(_list(value, context)):
        location = f"{context}[{index}]"
        invocation = _object(item, location)
        if invocation.get("executionSuccessful") is not True:
            _fail(f"{location}: scanner invocation did not succeed")
        if "exitCode" in invocation and (
            type(invocation["exitCode"]) is not int or invocation["exitCode"] != 0
        ):
            _fail(f"{location}: scanner invocation has an unsuccessful exit code")
        if "exitSignalNumber" in invocation and (
            type(invocation["exitSignalNumber"]) is not int
            or invocation["exitSignalNumber"] != 0
        ):
            _fail(f"{location}: scanner invocation exited on a signal")
        for key in ("processStartFailureMessage", "exitSignalName"):
            if key in invocation and (
                not isinstance(invocation[key], str) or invocation[key]
            ):
                _fail(f"{location}: scanner invocation reports {key}")
        for key in ("toolExecutionNotifications", "toolConfigurationNotifications"):
            if key in invocation:
                _notifications(invocation[key], f"{location}.{key}")


def _read_json(path: Path, kind: str) -> dict:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        message = f"{path}: cannot read {kind}: {exc}"
        raise StaticAnalysisError(message) from exc
    return _object(document, str(path))


def _report(path: Path) -> int:
    report = _read_json(path, "SARIF")
    if report.get("version") != "2.1.0":
        _fail(f"{path}: expected SARIF version 2.1.0")
    runs = _list(report.get("runs"), f"{path}.runs")
    if not runs:
        _fail(f"{path}: SARIF contains no analysis runs")
    for index, item in enumerate(runs):
        context = f"{path}.runs[{index}]"
        run = _object(item, context)
        tool = _object(run.get("tool"), f"{context}.tool")
        driver = _object(tool.get("driver"), f"{context}.tool.driver")
        name = driver.get("name")
        if not isinstance(name, str) or not name.strip():
            _fail(f"{context}: scanner driver has no name")
        # CodeQL can omit both invocations and rules from valid clean reports.
        # If invocations are supplied, their success evidence is mandatory.
        if "invocations" in run:
            _invocations(run["invocations"], f"{context}.invocations")
        results = _list(run.get("results"), f"{context}.results")
        if results:
            _fail(f"{context}: {name} reported {len(results)} finding(s)")
    return len(runs)


def check_sarif(path: Path) -> dict[str, int]:
    """Validate one report or every recursively discovered ``*.sarif`` file."""
    reports = sorted(path.rglob("*.sarif")) if path.is_dir() else [path]
    if not reports:
        _fail(f"{path}: no SARIF reports found")
    runs = sum(_report(report) for report in reports)
    return {"files": len(reports), "runs": runs}


def check_bandit(path: Path) -> dict[str, int]:
    """Require explicit empty findings/errors and proof Bandit scanned code."""
    report = _read_json(path, "Bandit JSON")
    results = _list(report.get("results"), f"{path}.results")
    errors = _list(report.get("errors"), f"{path}.errors")
    if results:
        _fail(f"{path}: Bandit reported {len(results)} finding(s)")
    if errors:
        _fail(f"{path}: Bandit reported {len(errors)} scanner error(s)")
    metrics = _object(report.get("metrics"), f"{path}.metrics")
    totals = _object(metrics.get("_totals"), f"{path}.metrics._totals")
    lines = totals.get("loc")
    if type(lines) is not int or lines <= 0:
        _fail(f"{path}: Bandit did not scan any source lines")
    return {"lines": lines}


def main(argv: Sequence[str] | None = None) -> int:
    """Validate saved static-analysis evidence and return a CI exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sarif = commands.add_parser("sarif", help="require complete, clean SARIF reports")
    sarif.add_argument("path", type=Path)
    bandit = commands.add_parser(
        "bandit", help="require a complete, clean Bandit report"
    )
    bandit.add_argument("path", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "bandit":
            counts = check_bandit(arguments.path)
            success = f"Bandit gate passed: {counts['lines']} source line(s) scanned"
        else:
            counts = check_sarif(arguments.path)
            success = f"SARIF gate passed: {counts['files']} report(s), {counts['runs']} run(s)"
    except StaticAnalysisError as exc:
        print(f"static analysis: {exc}", file=sys.stderr)
        return 1
    print(success)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
