"""UUID contention, independently checked histories and owned process isolation.

Forced-fallback cases use fixed entropy and frozen/rollback clocks, so exact
layout, rollover and uniqueness are deterministic statements about a reviewed
schedule. Real public/native cases only observe finite uniqueness and RFC shape;
they do not turn a collision-free sample into a probabilistic uniqueness proof
or claim a native monotonicity/threading guarantee beyond the public contract.
"""

from __future__ import annotations

from copy import deepcopy
from importlib import metadata
import json
import os
import signal
from typing import TYPE_CHECKING
from uuid import RFC_4122, UUID

import pytest

from tests.support.backends import require_backend
from tests.support.chaos import ordered
from tests.support.concurrency_ids import (
    BASE_MS,
    TAIL,
    fallback_model,
    validate_history,
)
from tests.support.process import run_python


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.chaos import ChaosJournal


def _run(scenario: str, config: dict, directory: Path, journal: ChaosJournal) -> dict:
    journal.record("ids.plan", scenario=scenario, config=config)
    completed = run_python(
        "from tests.support.concurrency_ids import main; main()",
        (scenario, json.dumps(config)),
        cwd=directory,
        timeout=18,
    )
    journal.record(
        "ids.worker_exit",
        returncode=completed.returncode,
        stderr=completed.stderr[:2048],
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    journal.record("ids.observed", observations=observed)
    return observed


@pytest.mark.parametrize("threads", [2, 4, 6])
@pytest.mark.parametrize("initial", [0, 4094], ids=["in-range", "overflow"])
@pytest.mark.parametrize("profile", ["frozen", "rollback"])
def test_fallback_thread_contention_matches_exact_model_and_interval_order(
    threads: int,
    initial: int,
    profile: str,
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: ChaosJournal,
) -> None:
    count = 16
    observed = _run(
        "threads",
        {
            "forced_fallback": True,
            "threads": threads,
            "count": count,
            "profile": profile,
            "initial_counter": initial,
            "order": ordered(
                concurrency_seed,
                f"concurrency.ids.{profile}.{initial}.{threads}",
                tuple(range(threads)),
            ),
        },
        tmp_path,
        concurrency_journal,
    )
    assert observed["failures"] == []
    assert observed["alive"] == []
    assert observed["backend"].endswith("._stdlib_uuid7")
    expected = fallback_model(count=threads * count, initial_counter=initial)
    assert validate_history(observed["history"], expected=expected) == {
        "count": threads * count,
        "threads": threads,
        "events": 2 * threads * count,
    }
    assert observed["state"] == [expected[-1] >> 80, (expected[-1] >> 64) & 0xFFF]
    # Tail barriers keep every call in a round open until all reservations exist.
    # This verifies genuine overlap even if the host otherwise runs one thread
    # at a time; no assertion uses the order that results were appended.
    for index in range(count):
        rows = [row for row in observed["history"] if row["sequence"] == index]
        assert max(row["start"] for row in rows) < min(row["end"] for row in rows)


def test_overlapping_reservations_may_complete_in_reverse_order(
    tmp_path: Path, concurrency_journal: ChaosJournal
) -> None:
    observed = _run("overlap", {}, tmp_path, concurrency_journal)
    assert observed["ready"] is True
    assert observed["alive"] is False
    assert observed["failures"] == []
    assert [row["thread"] for row in observed["history"]] == [1, 0]
    expected = fallback_model(count=2, initial_counter=10)
    assert validate_history(observed["history"], expected=expected)["count"] == 2
    assert [UUID(row["text"]).int for row in observed["history"]] == expected[::-1]


def _authored_history():
    values = fallback_model(count=3, initial_counter=0)
    rows = []
    for index, (thread, sequence) in enumerate(((0, 0), (1, 0), (0, 1))):
        text = str(UUID(int=values[index]))
        rows.append(
            {
                "thread": thread,
                "sequence": sequence,
                "api": "new",
                "start": 2 * index + 1,
                "end": 2 * index + 2,
                "text": text,
                "raw": text,
            }
        )
    return rows, values


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate-value", "duplicate UUID"),
        ("duplicate-identity", "duplicate invocation identity"),
        ("same-thread-inversion", "per-thread UUID order"),
        ("real-time-inversion", "real-time edge"),
        ("invalid-interval", "response precedes invocation"),
        ("wrong-version", "UUIDv7 layout"),
        ("noncanonical", "canonical lowercase"),
        ("wrong-hex", "bare hex API"),
        ("missing-call", "independent model"),
        ("counter-gap", "independent model"),
    ],
)
def test_history_oracle_rejects_deliberately_wrong_results(
    mutation: str, message: str, concurrency_journal: ChaosJournal
) -> None:
    history, expected = _authored_history()
    assert validate_history(history, expected=expected)["count"] == 3
    wrong = deepcopy(history)
    if mutation == "duplicate-value":
        wrong[1].update(text=wrong[0]["text"], raw=wrong[0]["raw"])
    elif mutation == "duplicate-identity":
        wrong[1].update(thread=0, sequence=0)
    elif mutation in {"same-thread-inversion", "real-time-inversion"}:
        other = 2 if mutation == "same-thread-inversion" else 1
        for field in ("text", "raw"):
            wrong[0][field], wrong[other][field] = wrong[other][field], wrong[0][field]
    elif mutation == "invalid-interval":
        wrong[0]["end"] = wrong[0]["start"]
    elif mutation == "wrong-version":
        wrong[0]["text"] = wrong[0]["raw"] = wrong[0]["text"].replace(
            "-7001-", "-4001-"
        )
    elif mutation == "noncanonical":
        wrong[0]["text"] = wrong[0]["raw"] = wrong[0]["text"].upper()
    elif mutation == "wrong-hex":
        wrong[0]["api"] = "new_hex"
    elif mutation == "missing-call":
        wrong.pop()
    elif mutation == "counter-gap":
        wrong[2]["text"] = wrong[2]["raw"] = str(UUID(int=expected[2] + (1 << 64)))
    concurrency_journal.record("ids.oracle_mutation", mutation=mutation, history=wrong)
    # Omitting the exact sequence check lets the ordering canaries demonstrate
    # that the interval/program-order checks themselves reject the corruption.
    exact = (
        None
        if mutation in {"same-thread-inversion", "real-time-inversion"}
        else expected
    )
    with pytest.raises(AssertionError, match=message):
        validate_history(wrong, expected=exact)


def test_public_backend_concurrent_shape_and_finite_uniqueness(
    tmp_path: Path, concurrency_seed: int, concurrency_journal: ChaosJournal
) -> None:
    observed = _run(
        "threads",
        {
            "forced_fallback": False,
            "threads": 4,
            "count": 24,
            "profile": "real-clock",
            "initial_counter": 0,
            "order": ordered(concurrency_seed, "concurrency.ids.public", (0, 1, 2, 3)),
        },
        tmp_path,
        concurrency_journal,
    )
    assert observed["failures"] == []
    assert observed["alive"] == []
    assert validate_history(observed["history"], monotonic=False)["count"] == 96
    assert observed["backend"].endswith((".uuid7", "._stdlib_uuid7"))


def test_real_native_backend_is_exercised_under_concurrent_calls(
    tmp_path: Path, concurrency_journal: ChaosJournal
) -> None:
    require_backend("uuid_utils")
    observed = _run(
        "threads",
        {
            "forced_fallback": False,
            "threads": 4,
            "count": 24,
            "profile": "real-clock",
            "initial_counter": 0,
            "order": [3, 1, 0, 2],
        },
        tmp_path,
        concurrency_journal,
    )
    concurrency_journal.record(
        "ids.native_capability",
        distribution="uuid-utils",
        version=metadata.version("uuid-utils"),
        binding=observed["backend"],
    )
    assert observed["backend"].startswith("uuid_utils.")
    assert observed["failures"] == []
    assert observed["alive"] == []
    assert validate_history(observed["history"], monotonic=False)["count"] == 96


@pytest.mark.parametrize("processes", [2, 4])
def test_spawn_has_fresh_counter_state_while_parent_lock_is_held(
    processes: int,
    tmp_path: Path,
    concurrency_seed: int,
    concurrency_journal: ChaosJournal,
) -> None:
    observed = _run(
        "spawn",
        {
            "order": ordered(
                concurrency_seed,
                f"concurrency.ids.spawn.{processes}",
                tuple(range(processes)),
            )
        },
        tmp_path,
        concurrency_journal,
    )
    assert observed["exitcodes"] == [0] * processes
    assert observed["parent"] == [BASE_MS + 777, 999, True]
    assert len({child["pid"] for child in observed["children"]}) == processes
    all_values = []
    for child in observed["children"]:
        assert "error" not in child
        assert child["before"] == [None, 0, False]
        assert child["backend"].endswith("._stdlib_uuid7")
        tail = int.from_bytes(bytes([child["index"] + 1]) * 8, "big") & ((1 << 62) - 1)
        parsed = [UUID(value) for value in child["values"]]
        # Each child uses a known disjoint entropy tail. This proves isolation
        # and layout without making a claim based on random collision odds.
        expected = [
            BASE_MS << 80 | 7 << 76 | (3 + index) << 64 | 2 << 62 | tail
            for index in range(32)
        ]
        assert [value.int for value in parsed] == expected
        assert all(value.variant == RFC_4122 and value.version == 7 for value in parsed)
        all_values.extend(value.int for value in parsed)
    assert len(set(all_values)) == processes * 32


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork contract")
def test_fork_replaces_a_vanished_threads_lock_and_reseeds_child_state(
    tmp_path: Path, concurrency_seed: int, concurrency_journal: ChaosJournal
) -> None:
    observed = _run("fork", {"seed": concurrency_seed}, tmp_path, concurrency_journal)
    assert observed["ready"] is True
    assert observed["status"] == 0
    assert observed["holder_alive"] is False
    assert observed["parent"] == [BASE_MS, 91, True]
    assert observed["backend_before"] == observed["backend_after"]
    assert observed["child"]["before"] == [None, 0, False]
    assert observed["child"]["backend"].endswith("._stdlib_uuid7")
    values = [UUID(value).int for value in observed["child"]["values"]]
    assert values == [
        BASE_MS << 80 | 7 << 76 | counter << 64 | 2 << 62 | TAIL for counter in (19, 20)
    ]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork contract")
def test_fork_deadlock_canary_is_killed_and_reaped_within_a_fixed_deadline(
    tmp_path: Path, concurrency_seed: int, concurrency_journal: ChaosJournal
) -> None:
    observed = _run(
        "fork",
        {"seed": concurrency_seed, "poison_lock": True},
        tmp_path,
        concurrency_journal,
    )
    assert observed["ready"] is True
    assert observed["status"] == -signal.SIGALRM
    assert observed["child"] is None
    assert observed["holder_alive"] is False
    assert observed["parent"] == [BASE_MS, 91, True]
    assert observed["backend_before"] == observed["backend_after"]
