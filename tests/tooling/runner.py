"""Validate suite ownership and run the inventory's executable suites."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING, cast

from tests.support.anchors import REPO_ROOT


if TYPE_CHECKING:
    from collections.abc import Sequence


class InventoryError(ValueError):
    """A suite inventory cannot support an honest test run."""


@dataclass(frozen=True)
class Suite:
    """One owned evidence lane and its declared implementation status."""

    id: str
    status: str
    description: str
    paths: tuple[str, ...]
    marker: str


def _read_toml(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read {path}: {exc}"
        raise InventoryError(message) from exc


def _markers(repo_root: Path) -> set[str]:
    config: object = _read_toml(repo_root / "pyproject.toml")
    for key in ("tool", "pytest", "ini_options"):
        if not isinstance(config, dict):
            break
        config = config.get(key)
    if not isinstance(config, dict) or not isinstance(config.get("markers"), list):
        message = "pyproject.toml must register pytest markers"
        raise InventoryError(message)
    markers = config["markers"]
    if any(not isinstance(marker, str) for marker in markers):
        message = "pytest markers must be strings"
        raise InventoryError(message)
    return {marker.partition(":")[0].strip() for marker in markers}


def _text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        message = f"suite {key} must be a nonempty string without surrounding spaces"
        raise InventoryError(message)
    return value


def _suite_path(repo_root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
        or path.parts[:2] != ("tests", "suites")
    ):
        message = f"suite path must be canonical and beneath tests/suites: {value!r}"
        raise InventoryError(message)
    resolved = (repo_root / value).resolve()
    if not resolved.is_relative_to((repo_root / "tests/suites").resolve()):
        message = f"suite path escapes tests/suites: {value!r}"
        raise InventoryError(message)
    return resolved


def load_inventory(repo_root: Path) -> tuple[Suite, ...]:
    """Read and validate the inventory schema and registered marker names."""
    data = _read_toml(repo_root / "tests/tooling/inventory.toml")
    if set(data) != {"version", "suite"} or type(data.get("version")) is not int:
        message = "inventory requires version = 1 and [[suite]] records"
        raise InventoryError(message)
    if data["version"] != 1:
        message = f"unsupported inventory version: {data['version']!r}"
        raise InventoryError(message)
    rows = data["suite"]
    if not isinstance(rows, list) or not rows:
        message = "inventory must contain at least one [[suite]]"
        raise InventoryError(message)
    allowed_markers = _markers(repo_root)
    suites: list[Suite] = []
    seen: set[str] = set()
    for entry in rows:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "status",
            "description",
            "paths",
            "marker",
        }:
            message = "each suite requires id, status, description, paths, and marker"
            raise InventoryError(message)
        row = cast("dict[str, object]", entry)
        identity = _text(row, "id")
        if identity in seen:
            message = f"duplicate suite id: {identity}"
            raise InventoryError(message)
        seen.add(identity)
        status = _text(row, "status")
        if status not in {"active", "planned"}:
            message = f"suite {identity}: status must be active or planned"
            raise InventoryError(message)
        marker = _text(row, "marker")
        if marker not in allowed_markers:
            message = f"suite {identity}: unregistered pytest marker {marker!r}"
            raise InventoryError(message)
        paths = row["paths"]
        if not isinstance(paths, list) or not paths:
            message = f"suite {identity}: paths must be a nonempty list"
            raise InventoryError(message)
        if any(not isinstance(path, str) for path in paths):
            message = f"suite {identity}: paths must contain strings"
            raise InventoryError(message)
        suite_paths = tuple(cast("list[str]", paths))
        for path in suite_paths:
            _suite_path(repo_root, path)
        suites.append(
            Suite(identity, status, _text(row, "description"), suite_paths, marker)
        )
    return tuple(suites)


def _test_files(path: Path) -> set[Path]:
    files: set[Path] = set()
    for file in path.rglob("test_*.py"):
        if not file.is_file() or "__pycache__" in file.relative_to(path).parts:
            continue
        resolved = file.resolve()
        if not resolved.is_relative_to(path.resolve()):
            message = f"test file escapes its suite directory: {file}"
            raise InventoryError(message)
        files.add(resolved)
    return files


def check_inventory(repo_root: Path) -> tuple[Suite, ...]:
    """Verify paths, nonempty active suites, and exactly one owner per test."""
    suites = load_inventory(repo_root)
    owned: dict[Path, str] = {}
    declared: list[tuple[Path, str]] = []
    for suite in suites:
        for value in suite.paths:
            path = _suite_path(repo_root, value)
            if not path.is_dir():
                message = f"suite {suite.id}: missing suite directory {value}"
                raise InventoryError(message)
            for previous, owner in declared:
                if path.is_relative_to(previous) or previous.is_relative_to(path):
                    message = (
                        f"overlapping suite paths: {owner} and {suite.id} at {value}"
                    )
                    raise InventoryError(message)
            declared.append((path, suite.id))
            files = _test_files(path)
            if suite.status == "active" and not files:
                message = (
                    f"suite {suite.id}: active path contains no test_*.py: {value}"
                )
                raise InventoryError(message)
            if suite.status == "planned" and files:
                message = (
                    f"suite {suite.id}: planned path contains test_*.py; "
                    "activate the suite before adding executable coverage"
                )
                raise InventoryError(message)
            owned.update(dict.fromkeys(files, suite.id))
    unowned = _test_files(repo_root / "tests") - owned.keys()
    if unowned:
        paths = ", ".join(sorted(str(path.relative_to(repo_root)) for path in unowned))
        message = f"test files have no suite owner: {paths}"
        raise InventoryError(message)
    return suites


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    """List, validate, or run one suite while preserving pytest's exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show active and planned evidence lanes")
    commands.add_parser("check", help="validate the inventory against the test tree")
    run = commands.add_parser("run", help="run an active suite through pytest")
    run.add_argument("suite", help="suite id from the inventory")
    run.add_argument(
        "pytest_args", nargs=argparse.REMAINDER, help="arguments for pytest"
    )
    args = parser.parse_args(argv)
    root = (repo_root or REPO_ROOT).resolve()
    try:
        suites = (
            load_inventory(root) if args.command == "list" else check_inventory(root)
        )
    except InventoryError as exc:
        print(f"suite inventory: {exc}", file=sys.stderr)
        return 2
    if args.command == "list":
        for suite in suites:
            print(f"{suite.id}\t{suite.status}\t{suite.description}")
        return 0
    if args.command == "check":
        active = sum(suite.status == "active" for suite in suites)
        print(f"suite inventory valid: {active} active, {len(suites) - active} planned")
        return 0
    selected = next((suite for suite in suites if suite.id == args.suite), None)
    if selected is None:
        print(
            f"unknown suite: {args.suite!r}; use 'list' to inspect the inventory",
            file=sys.stderr,
        )
        return 2
    if selected.status != "active":
        print(
            f"suite {selected.id} is planned; no executable coverage is claimed",
            file=sys.stderr,
        )
        return 2
    extra = args.pytest_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    selection = selected.marker
    if selection not in {"perf", "chaos"}:
        selection += " and not perf and not chaos"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *selected.paths,
        "-m",
        selection,
        *extra,
    ]
    # The executable is fixed; validated paths and user pytest arguments stay literal.
    return subprocess.run(command, cwd=root, check=False).returncode  # noqa: S603
