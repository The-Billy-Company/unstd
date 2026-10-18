"""Every reviewed input completes its real oracle in a fresh bounded worker.

Installed targets require their declared extensions; local missing capabilities
skip explicitly and --require-extras makes them fatal. Applicable stdlib workers
block optional imports before target loading, even in an all-extra environment.
Manifest authentication, exact case counts and normal worker exit prevent empty,
partially replayed or silently degraded corpora from becoming passing evidence.
"""

from __future__ import annotations

import json

import pytest

from tests.support.backends import require_backend
from tests.tooling.fuzz.execution import run_inputs
from tests.tooling.fuzz.registry import load_registry, load_seeds


_CASES = tuple(
    (target, backend) for target in load_registry() for backend in target.backends
)


@pytest.mark.parametrize(
    ("target", "backend"),
    _CASES,
    ids=[f"{target.name}-{backend}" for target, backend in _CASES],
)
def test_reviewed_committed_corpus_completes_every_oracle(target, backend, tmp_path):
    if backend == "installed":
        for requirement in target.requirements:
            require_backend(requirement)
    seeds = load_seeds(target)
    report = run_inputs(
        target.name,
        ((seed.identity, seed.data) for seed in seeds),
        backend=backend,
        wall_seconds=20,
        cpu_seconds=10,
        memory_mb=1024,
        per_input_seconds=3,
        max_input_bytes=target.max_input_bytes,
        artifact_dir=tmp_path / target.name / backend,
    )
    assert report["status"] == "passed", json.dumps(report, indent=2, sort_keys=True)
    assert report["cases"] == len(seeds)
    assert report["attempted"] == len(seeds)
    assert report["completed"] == len(seeds)
    assert report["worker_returncode"] == 0
    expected_modules = set(target.requirements) if backend == "installed" else set()
    assert set(report["backend_modules"]) == expected_modules
