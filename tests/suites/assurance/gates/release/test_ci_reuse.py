"""Release reuse requires fresh successful CI for the exact trusted commit."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.tooling.release import reuse_ci


NOW = datetime(2026, 10, 21, 23, tzinfo=UTC)
SHA = "a" * 40
REPO = "The-Billy-Company/unstd"


def _run(**changes):
    return {
        "id": 123,
        "head_sha": SHA,
        "head_repository": {"full_name": REPO},
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/The-Billy-Company/unstd/actions/runs/123",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"head_sha": "b" * 40},
        {"event": "pull_request"},
        {"path": ".github/workflows/other.yml"},
        {"head_repository": {"full_name": "outside/unstd"}},
        {"created_at": (NOW - timedelta(hours=2)).isoformat()},
        {"created_at": (NOW + timedelta(minutes=1)).isoformat()},
    ],
)
def test_unrelated_or_stale_runs_require_the_full_gate(changes):
    result = reuse_ci.resolve(
        lambda _: {"workflow_runs": [_run(**changes)]},
        sha=SHA,
        repository=REPO,
        now=NOW,
    )
    assert result is None


def test_successful_exact_commit_ci_is_reused_without_a_second_run():
    run = _run()
    assert (
        reuse_ci.resolve(
            lambda _: {"workflow_runs": [run]}, sha=SHA, repository=REPO, now=NOW
        )
        == run
    )


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out"])
def test_existing_unsuccessful_ci_blocks_publication(conclusion):
    with pytest.raises(reuse_ci.CIReuseError, match="CI did not succeed"):
        reuse_ci.resolve(
            lambda _: {"workflow_runs": [_run(conclusion=conclusion)]},
            sha=SHA,
            repository=REPO,
            now=NOW,
        )


def test_pending_ci_is_waited_for_and_revalidated():
    requests, waits = [], []

    def fetch(endpoint):
        requests.append(endpoint)
        return (
            {"workflow_runs": [_run(status="in_progress", conclusion=None)]}
            if len(requests) == 1
            else _run()
        )

    assert (
        reuse_ci.resolve(fetch, sha=SHA, repository=REPO, now=NOW, sleep=waits.append)
        == _run()
    )
    assert waits == [15]
    assert requests[-1] == "actions/runs/123"


def test_newer_failed_attempt_cannot_be_hidden_by_older_success():
    with pytest.raises(reuse_ci.CIReuseError, match="CI did not succeed"):
        reuse_ci.resolve(
            lambda _: {"workflow_runs": [_run(), _run(id=124, conclusion="failure")]},
            sha=SHA,
            repository=REPO,
            now=NOW,
        )


def test_run_identity_is_rechecked_after_waiting():
    responses = iter(
        [
            {"workflow_runs": [_run(status="in_progress", conclusion=None)]},
            _run(head_sha="b" * 40),
        ]
    )
    with pytest.raises(reuse_ci.CIReuseError, match="identity changed"):
        reuse_ci.resolve(
            lambda _: next(responses),
            sha=SHA,
            repository=REPO,
            now=NOW,
            sleep=lambda _: None,
        )


def test_failed_rerun_of_an_older_run_blocks_newer_but_earlier_success():
    with pytest.raises(reuse_ci.CIReuseError, match="CI did not succeed"):
        reuse_ci.resolve(
            lambda _: {
                "workflow_runs": [
                    _run(
                        id=124,
                        run_started_at=(NOW - timedelta(minutes=2)).isoformat(),
                        updated_at=(NOW - timedelta(minutes=1)).isoformat(),
                    ),
                    _run(id=123, run_started_at=NOW.isoformat(), conclusion="failure"),
                ]
            },
            sha=SHA,
            repository=REPO,
            now=NOW,
        )


def test_pending_ci_has_a_finite_wait_budget():
    run = _run(status="in_progress", conclusion=None)
    waits = []
    with pytest.raises(reuse_ci.CIReuseError, match="incomplete after 20 minutes"):
        reuse_ci.resolve(
            lambda path: {"workflow_runs": [run]} if "?" in path else run,
            sha=SHA,
            repository=REPO,
            now=NOW,
            sleep=waits.append,
        )
    assert sum(waits) <= 22 * 60


def test_old_late_completion_cannot_hide_a_new_pending_attempt():
    pending = _run(
        id=124,
        run_started_at=(NOW - timedelta(minutes=2)).isoformat(),
        updated_at=(NOW - timedelta(minutes=1)).isoformat(),
        status="in_progress",
        conclusion=None,
    )
    old = _run(id=123, run_started_at=(NOW - timedelta(minutes=5)).isoformat())
    responses = iter(
        [
            {"workflow_runs": [old, pending]},
            {**pending, "status": "completed", "conclusion": "failure"},
        ]
    )
    with pytest.raises(reuse_ci.CIReuseError, match="CI did not succeed"):
        reuse_ci.resolve(
            lambda _: next(responses),
            sha=SHA,
            repository=REPO,
            now=NOW,
            sleep=lambda _: None,
        )
