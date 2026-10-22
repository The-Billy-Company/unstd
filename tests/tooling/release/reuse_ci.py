"""Reuse recent exact-commit CI evidence before rebuilding release artifacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen


class CIReuseError(ValueError):
    """Existing CI evidence is unsuccessful or cannot be authenticated."""


def eligible(run: dict, *, sha: str, repository: str, now: datetime) -> bool:
    """Admit only recent repository-owned runs of this commit's CI workflow."""
    return (
        run.get("head_sha") == sha
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("head_repository", {}).get("full_name") == repository
        and now - timedelta(hours=1) <= datetime.fromisoformat(run["created_at"]) <= now
    )


def resolve(
    fetch, *, sha: str, repository: str, now=None, sleep=time.sleep
) -> dict | None:
    """Wait for existing CI, or request the full reusable gate when none exists."""
    now = datetime.now(UTC) if now is None else now
    listing = fetch(f"actions/workflows/ci.yml/runs?head_sha={sha}&per_page=100")
    runs = [
        run
        for run in listing["workflow_runs"]
        if eligible(run, sha=sha, repository=repository, now=now)
    ]
    if not runs:
        return None
    # Reruns retain their original ID. Order attempts by start time, so a late
    # completion of an old run cannot hide a newer pending or failed attempt.
    run = max(
        runs,
        key=lambda item: (
            datetime.fromisoformat(item.get("run_started_at") or item["created_at"]),
            datetime.fromisoformat(item["updated_at"]),
            item["id"],
        ),
    )
    for _ in range(81):
        if not eligible(run, sha=sha, repository=repository, now=now):
            message = "CI run identity changed while waiting"
            raise CIReuseError(message)
        if run["status"] == "completed":
            if run["conclusion"] != "success":
                message = f"CI did not succeed: {run['html_url']} ({run['conclusion']})"
                raise CIReuseError(message)
            return run
        sleep(15)
        run = fetch(f"actions/runs/{run['id']}")
    message = f"CI is still incomplete after 20 minutes: {run['html_url']}"
    raise CIReuseError(message)


def main() -> None:
    """Resolve fresh CI evidence through the read-only GitHub Actions API."""
    repository, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    token = os.environ["GITHUB_TOKEN"]

    def fetch(endpoint):
        request = Request(
            f"https://api.github.com/repos/{repository}/{endpoint}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 — fixed HTTPS GitHub API
            return json.load(response)

    run = resolve(fetch, sha=sha, repository=repository)
    reused = "true" if run is not None else "false"
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"reused={reused}\n")
    message = (
        f"Reusing recent successful CI for `{sha}`: {run['html_url']}"
        if run is not None
        else f"No recent CI for `{sha}`; running the complete reusable gate."
    )
    print(message)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(message + "\n")


if __name__ == "__main__":
    main()
