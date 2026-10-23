"""Identify reusable release dependencies independently of the local version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


def cache_key(root: Path) -> str:
    """Hash the complete locked graph and build configuration for cache reuse."""
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    with (root / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)
    name = project["project"]["name"]
    roots = [
        package
        for package in lock["package"]
        if package["name"] == name and package["source"] == {"editable": "."}
    ]
    if len(roots) != 1:
        message = "release cache requires exactly one editable root package"
        raise ValueError(message)

    # unstd itself is rebuilt from the release sdist every time. Its version
    # cannot change a cached third-party wheel, whereas every dependency edge,
    # artifact hash and build/frontend setting can. Keep the complete inputs
    # instead of selecting only upstream versions or a partial dependency graph.
    project["project"].pop("version", None)
    roots[0].pop("version", None)
    payload = {"schema": 1, "project": project, "lock": lock}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> None:
    """Print the dependency digest and optionally expose it to GitHub Actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT")
    )
    arguments = parser.parse_args(argv)
    key = cache_key(arguments.root)
    print(key)
    if arguments.github_output is not None:
        with arguments.github_output.open("a", encoding="utf-8") as output:
            output.write(f"key={key}\n")


if __name__ == "__main__":
    main()
