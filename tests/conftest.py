"""Shared profiles, suite selection, and optional-backend collection policy."""

from __future__ import annotations

import importlib
from importlib import metadata
import tomllib
from typing import TYPE_CHECKING

from packaging.requirements import Requirement
import pytest

from tests.support.anchors import REPO_ROOT
from tests.support.backends import configure_backend_policy
from tests.support.hypothesis.profiles import load_active_profile


if TYPE_CHECKING:
    from pathlib import Path


_PROFILE = load_active_profile()
with (REPO_ROOT / "tests/tooling/inventory.toml").open("rb") as _stream:
    _SUITES = tomllib.load(_stream)["suite"]

# Refuse collection only for modules that cannot exist without their extra.
# TLS is pure stdlib: its contracts must still run without the crypto extra.
_NEEDS: dict[str, str] = {
    "suites/product/crypto/unit/test_vectors.py": "unstd.crypto.digest",
    "suites/product/crypto/security/test_adverse.py": "unstd.crypto.digest",
    "suites/product/crypto/security/test_token_boundary.py": "unstd.crypto.token",
    "suites/product/model/**/*.py": "unstd.model",
    "suites/product/serde/unit/test_structs.py": "unstd.serde.structs",
    "suites/product/time/unit/test_zoned.py": "unstd.time.zoned",
    "suites/assurance/gates/bench/test_bench.py": "bench",
}


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


_MISSING = {
    pattern: module for pattern, module in _NEEDS.items() if not _importable(module)
}
collect_ignore_glob = list(_MISSING)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Arm the required-backend posture for all-extra and dependency-floor CI."""
    parser.addoption(
        "--require-extras",
        action="store_true",
        help="fail if any declared extra is absent",
    )


def pytest_configure(config: pytest.Config) -> None:
    """A required capability must not disappear behind import skips or fallback."""
    required = config.getoption("--require-extras")
    configure_backend_policy(required=required)
    if not required:
        return
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        extras = tomllib.load(stream)["project"]["optional-dependencies"]
    missing = list(_MISSING.values())
    for extra, requirements in extras.items():
        for text in requirements:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
                continue
            try:
                version = metadata.version(requirement.name)
            except metadata.PackageNotFoundError:
                missing.append(text)
            else:
                if not requirement.specifier.contains(version, prereleases=True):
                    missing.append(f"{text} (installed {version})")
    if missing:
        msg = f"required extras unavailable: {', '.join(sorted(set(missing)))}"
        raise pytest.UsageError(msg)


def _owns(path: Path, candidate: str) -> bool:
    return path.is_relative_to(REPO_ROOT / candidate)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every collected test exactly one inventoried owner and kind marker."""
    for item in items:
        matches = [
            suite
            for suite in _SUITES
            if suite["status"] == "active"
            and any(_owns(item.path, candidate) for candidate in suite["paths"])
        ]
        if len(matches) != 1:
            msg = f"{item.nodeid}: expected one active suite owner, found {len(matches)}; update tests/tooling/inventory.toml"
            raise pytest.UsageError(msg)
        item.add_marker(matches[0]["marker"])
        # Some retained API/security/parity files also contain generated tests.
        # Marker selection includes those without moving individual functions.
        if getattr(getattr(item, "obj", None), "is_hypothesis_test", False):
            item.add_marker("properties")


def pytest_report_header() -> str:
    """Make the selected generation policy visible in verbose runs."""
    return f"unstd Hypothesis profile: {_PROFILE}; suite inventory: tests/tooling/inventory.toml"
