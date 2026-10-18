"""Reviewed child-only controls for campaign failure and cleanup evidence."""

from __future__ import annotations

import mmap
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.support.chaos import ChaosJournal, journal_sink


pytestmark = pytest.mark.chaos
MODE = os.environ.get("UNSTD_CHAOS_GATE_FIXTURE", "empty")


@pytest.fixture(autouse=True)
def reviewed_evidence(request):
    if MODE != "no-journal":
        seed = request.config.getoption("--chaos-seed")
        journal = ChaosJournal(
            seed,
            request.node.nodeid,
            sink=journal_sink(
                getattr(request.config, "_unstd_chaos_journal_directory", None)
                if MODE != "invalid-journal"
                else None,
                request.node.nodeid,
            ),
        )
        journal.record("reviewed.fixture", fixture=MODE, seed=seed)
        snapshot = journal.snapshot()
        if MODE == "invalid-journal":
            snapshot["seed"] += 1
        request.node.user_properties.extend(
            [
                ("chaos_seed", seed),
                ("chaos_journal", snapshot),
            ]
        )
        return journal
    return None


if MODE != "empty":

    def test_reviewed_campaign_control(capsys, reviewed_evidence):
        if MODE in {"hang", "abort", "descendant", "cpu"}:
            reviewed_evidence.record("reviewed.worker.observed", pid=os.getpid())
            reviewed_evidence.record("reviewed.fault.armed", fault=MODE)
        if MODE == "assertion":
            assert MODE == "accepted", "reviewed campaign assertion failure"
        elif MODE == "hang":
            time.sleep(30)
        elif MODE == "abort":
            os.abort()
        elif MODE == "flood":
            with capsys.disabled():
                sys.stdout.write("reviewed stdout head\n" + "o" * 200000)
                sys.stdout.write("\nreviewed stdout tail\n")
                sys.stderr.write("reviewed stderr head\n" + "e" * 200000)
                sys.stderr.write("\nreviewed stderr tail\n")
                sys.stdout.flush()
                sys.stderr.flush()
        elif MODE == "descendant":
            child = subprocess.Popen(
                [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
            )
            Path(os.environ["UNSTD_CHAOS_DESCENDANT_PID"]).write_text(str(child.pid))
            time.sleep(30)
        elif MODE == "skip":
            pytest.skip("reviewed unavailable capability")
        elif MODE == "cpu":
            while True:
                pass
        elif MODE == "memory":
            # Untouched virtual pages avoid physically allocating 2 GiB if the
            # limiter breaks. A missing RLIMIT_AS still fails the control.
            with pytest.raises((OSError, ValueError)), mmap.mmap(-1, 2 * 1024**3):
                pass
        else:
            assert MODE in {"pass", "no-journal", "invalid-journal"}
