"""Build once per session and exercise each published-artifact build route."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.support.packaging.artifacts import (
    Artifacts,
    Consumer,
    build_artifacts,
    create_consumer,
)


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Artifacts:
    return build_artifacts(tmp_path_factory.mktemp("packaging-artifacts"))


@pytest.fixture(scope="session", params=["wheel", "sdist_wheel"])
def wheel(request: pytest.FixtureRequest, artifacts: Artifacts) -> Path:
    return getattr(artifacts, request.param)


@pytest.fixture(scope="session")
def consumer(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> Consumer:
    return create_consumer(tmp_path_factory.mktemp("packaging-consumer") / "app", wheel)
