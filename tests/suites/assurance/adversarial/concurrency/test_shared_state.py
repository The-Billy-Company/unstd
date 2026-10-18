"""Cold-cache races, reusable codec/match state, private parsers and HAMT forks.

Every schedule runs in a fresh process under a wall deadline, including oracle
canaries. The seed permutes actor admission; barriers enforce the observation
boundary. These are shared immutable baselines and separate caller-owned inputs,
not promises that arbitrary mutation or Python native-extension execution is
free-threaded. Broken-result canaries must fail the same production observations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.backends import require_backend
from tests.support.process import run_python


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.chaos import ChaosJournal


CASES = (
    ("clone_admission", None),
    ("clone_fields", None),
    ("clone_reentry", None),
    ("rex_eviction", None),
    ("rex_retained_matches", None),
    ("rex_callbacks", None),
    ("rex_callback_failure", None),
    ("codec_shared", "msgspec"),
    ("codec_policies", "msgspec"),
    ("yaml_iterators", "ruamel.yaml"),
    ("yaml_reentry", "ruamel.yaml"),
    ("yaml_failure_isolation", "ruamel.yaml"),
    ("map_forks", "immutables"),
)
_WORKER = "from tests.support.concurrency_shared_state import main; main()"
_BARRIERS = {
    "clone_admission": ["simultaneous cold class admission"],
    "clone_fields": ["simultaneous inherited field discovery"],
    "clone_reentry": ["all foreign copy hooks active before nested clone"],
    "rex_eviction": [
        "all native compilation attempts active at full cache capacity",
        "all actors select the same oldest key before any deletion",
    ],
    "rex_retained_matches": [
        "all matches retained",
        "purge completed before projection",
    ],
    "rex_callbacks": ["all outer callbacks active before nested search and purge"],
    "rex_callback_failure": ["healthy and raising callbacks both active"],
    "codec_shared": ["all encoded outputs retained", "all decoded outputs retained"],
    "codec_policies": ["strict and lax reusable decoders ready"],
    "yaml_iterators": ["all first documents retained", "all second documents retained"],
    "yaml_reentry": ["all outer reader callbacks active before nested YAML parse"],
    "yaml_failure_isolation": ["invalid and healthy parser callers ready"],
    "map_forks": ["all independently updated snapshots retained before delete"],
}


def _run(scenario, seed, mode, directory, journal):
    journal.record(
        "shared_state.plan",
        scenario=scenario,
        seed=seed,
        mode=mode,
        barriers=_BARRIERS[scenario],
        worker_wall_seconds=12,
        thread_deadline_seconds=5,
    )
    result = run_python(
        _WORKER,
        [scenario, str(seed), mode],
        cwd=directory,
        timeout=12,
    )
    journal.record(
        "shared_state.worker_exit",
        scenario=scenario,
        mode=mode,
        returncode=result.returncode,
        stderr=result.stderr[-4096:],
    )
    return result


@pytest.mark.parametrize(
    ("scenario", "backend"), CASES, ids=[case[0] for case in CASES]
)
def test_shared_state_interleaving(
    scenario: str,
    backend: str | None,
    concurrency_seed: int,
    concurrency_journal: ChaosJournal,
    tmp_path: Path,
):
    if backend is not None:
        require_backend(backend)
    result = _run(scenario, concurrency_seed, "normal", tmp_path, concurrency_journal)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["scenario"] == scenario
    assert report["observations"]
    assert report["events"]
    assert any(event["event"] == "workers.joined" for event in report["events"])
    concurrency_journal.record("shared_state.observed", **report)


@pytest.mark.parametrize(
    ("scenario", "backend"), CASES, ids=[case[0] for case in CASES]
)
def test_shared_state_oracle_rejects_broken_result(
    scenario: str,
    backend: str | None,
    concurrency_seed: int,
    concurrency_journal: ChaosJournal,
    tmp_path: Path,
):
    if backend is not None:
        require_backend(backend)
    result = _run(scenario, concurrency_seed, "broken", tmp_path, concurrency_journal)
    assert result.returncode != 0, "broken production observation passed its oracle"
    assert "AssertionError" in result.stderr, result.stderr
    assert "Traceback" in result.stderr
    if scenario in {"clone_admission", "clone_fields"}:
        assert "shared mutable output" in result.stderr, result.stderr
