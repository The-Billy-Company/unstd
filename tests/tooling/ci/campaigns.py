"""Keep batched campaign workers readable in GitHub Actions logs and summaries."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def campaign_group(title):
    """Fold one independently supervised worker without changing local output."""
    enabled = (
        os.environ.get("UNSTD_CAMPAIGN_REPORTING") == "1"
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    if enabled:
        print(f"::group::{title}", flush=True)
    try:
        yield
    finally:
        if enabled:
            print("::endgroup::", flush=True)


def append_campaign_summary(title, headings, rows):
    """Append worker outcomes and artifact references to the current job summary."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path or os.environ.get("UNSTD_CAMPAIGN_REPORTING") != "1":
        return
    lines = [
        f"### {title}",
        "",
        " | ".join(headings),
        " | ".join("---" for _ in headings),
    ]
    lines.extend(" | ".join(str(value) for value in row) for row in rows)
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n\n")
