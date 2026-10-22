"""The publication verifier accepts both real offline wheel build routes."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from tests.tooling.release.verify import verify


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.packaging.artifacts import Artifacts


def test_release_verifier_accepts_real_built_artifacts(
    artifacts: Artifacts, wheel: Path, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    shutil.copyfile(artifacts.sdist, dist / artifacts.sdist.name)
    shutil.copyfile(wheel, dist / wheel.name)
    report = verify(dist, f"v{artifacts.project['version']}")
    assert report["name"] == artifacts.project["name"]
    assert report["version"] == artifacts.project["version"]
    assert len(report["artifacts"]) == 2
