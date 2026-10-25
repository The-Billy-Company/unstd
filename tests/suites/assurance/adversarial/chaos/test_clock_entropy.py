"""Clock discontinuities and entropy outages with isolated, replayable recovery.

The UUID experiments exercise the documented Python fallback explicitly; a
Rust UUID backend owns its clock and cannot be redirected by Python patches.
Secure random tests inject the real stdlib entropy boundary, never a seeded
replacement for the implementation under test. A short-read experiment records
stdlib alias behavior; it does not claim validation beyond os.urandom's own
exact-length contract. Scratch-file faults expose the Python token consumer;
Linux getrandom failures are covered at the native filesystem syscall boundary.
Every global fault lives in a bounded fresh interpreter.
"""

from __future__ import annotations

import base64
import errno
import json
import subprocess
import sys
from typing import TYPE_CHECKING
from uuid import RFC_4122, UUID

import pytest

from tests.support.chaos.journal import ordered
from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.chaos.journal import ChaosJournal


BASE = 1_700_000_000_000
FAULTS = (errno.EIO, errno.EAGAIN, errno.EINTR)
_LAUNCH = (
    "import sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; "
    "from tests.support.chaos.clock_entropy import main; main()"
)


def _run(scenario: str, config: dict, journal: ChaosJournal, *, seed: int) -> dict:
    journal.record("clock_entropy.plan", scenario=scenario, config=config, seed=seed)
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and reviewed worker
        [
            sys.executable,
            "-I",
            "-c",
            _LAUNCH,
            str(REPO_ROOT / "src"),
            str(REPO_ROOT),
            scenario,
            json.dumps(config),
        ],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    journal.record(
        "clock_entropy.worker_exit",
        scenario=scenario,
        returncode=completed.returncode,
        stderr=completed.stderr[:4096],
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    journal.record("clock_entropy.observed", scenario=scenario, observations=observed)
    return observed


def _shape(observed: dict, timestamp: int, counter: int, *, tail: int) -> None:
    value = UUID(observed["text"])
    assert observed["timestamp"] == timestamp
    assert observed["counter"] == counter
    assert observed["tail"] == tail
    assert observed["version"] == 7
    assert observed["variant"] == RFC_4122
    # Authored RFC 9562 field layout, independent of the generator.
    assert value.int == (timestamp << 80 | 7 << 76 | counter << 64 | 2 << 62 | tail)


@pytest.mark.parametrize(
    ("name", "stamps", "seed", "count", "expected", "seed_requests"),
    [
        (
            "frozen",
            (BASE,),
            0,
            5,
            {0: (BASE, 0), 1: (BASE, 1), 2: (BASE, 2), 3: (BASE, 3), 4: (BASE, 4)},
            1,
        ),
        (
            "rollback",
            (BASE, BASE - 1, BASE - 1000, BASE - 86400000, BASE),
            19,
            5,
            {
                0: (BASE, 19),
                1: (BASE, 20),
                2: (BASE, 21),
                3: (BASE, 22),
                4: (BASE, 23),
            },
            1,
        ),
        (
            "jump_then_rollback",
            (BASE, BASE + 86400000, BASE + 1, BASE - 1, BASE + 86400001),
            2,
            5,
            {
                0: (BASE, 2),
                1: (BASE + 86400000, 2),
                2: (BASE + 86400000, 3),
                3: (BASE + 86400000, 4),
                4: (BASE + 86400001, 2),
            },
            3,
        ),
        (
            "frozen_counter_exhaustion",
            (BASE,),
            0,
            4100,
            {
                0: (BASE, 0),
                1: (BASE, 1),
                2050: (BASE, 2050),
                4098: (BASE + 1, 2),
                4099: (BASE + 1, 3),
            },
            2,
        ),
        (
            "repeated_counter_exhaustion_during_rollback",
            (BASE, BASE - 1, BASE - 2, BASE - 3, BASE - 4),
            0xFFF,
            5,
            {
                0: (BASE, 0xFFF),
                1: (BASE + 1, 0xFFF),
                2: (BASE + 2, 0xFFF),
                3: (BASE + 3, 0xFFF),
                4: (BASE + 4, 0xFFF),
            },
            5,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_uuid7_clock_faults_have_exact_layout_and_order(
    name: str,
    stamps: tuple[int, ...],
    seed: int,
    count: int,
    expected: dict[int, tuple[int, int]],
    seed_requests: int,
    chaos_seed: int,
    chaos_journal: ChaosJournal,
) -> None:
    observed = _run(
        "uuid_clock",
        {"stamps": stamps, "counter_seed": seed, "count": count, "profile": name},
        chaos_journal,
        seed=chaos_seed,
    )
    assert observed["count"] == count
    assert observed["strictly_increasing"] is True
    assert observed["unique"] is True
    assert observed["seed_requests"] == seed_requests
    assert observed["tail_requests"] == count
    assert observed["other_requests"] == []
    tail = int.from_bytes(b" " * 8, "big") & ((1 << 62) - 1)
    for index, (timestamp, counter) in expected.items():
        _shape(observed["checkpoints"][str(index)], timestamp, counter, tail=tail)
    assert observed["final_state"] == list(expected[count - 1])


def test_uuid7_seeded_discontinuity_schedule_is_replayable(
    chaos_seed: int, chaos_journal: ChaosJournal
) -> None:
    offsets = ordered(chaos_seed, "clock_entropy.uuid7", (-1000, -1, 0, 0, 1, 1000))
    stamps = [BASE, *(BASE + offset for offset in offsets), BASE + 1001]
    observed = _run(
        "uuid_clock",
        {"stamps": stamps, "counter_seed": 0, "count": len(stamps)},
        chaos_journal,
        seed=chaos_seed,
    )
    assert observed["stamps"] == stamps
    assert observed["strictly_increasing"] is True
    assert observed["unique"] is True
    assert observed["tail_requests"] == len(stamps)
    first = observed["checkpoints"]["0"]
    last = observed["checkpoints"][str(len(stamps) - 1)]
    tail = int.from_bytes(b" " * 8, "big") & ((1 << 62) - 1)
    _shape(first, BASE, 0, tail=tail)
    _shape(last, BASE + 1001, 0, tail=tail)


@pytest.mark.parametrize(
    "phase",
    [
        "initial_seed",
        "advanced_seed",
        "overflow_seed",
        "random_tail",
        "advanced_tail",
        "overflow_tail",
    ],
)
def test_uuid7_entropy_failures_preserve_error_and_recover(
    phase: str, chaos_seed: int, chaos_journal: ChaosJournal
) -> None:
    for code in ordered(chaos_seed, f"clock_entropy.uuid7.{phase}", FAULTS):
        observed = _run(
            "uuid_entropy",
            {"phase": phase, "errno": code},
            chaos_journal,
            seed=chaos_seed,
        )
        assert observed["error"] == {
            "same_instance": True,
            "type": type(OSError(code, "fault")).__name__,
            "errno": code,
        }
        assert observed["fault_count"] == 1
        assert observed["increasing_after_fault"] is True
        if phase.endswith("seed"):
            # A failed reseed must not publish a new timestamp/counter state.
            assert observed["state_before"] == observed["state_failed"]
        for value in observed["recovered"]:
            parsed = UUID(value["text"])
            assert parsed.version == 7
            assert parsed.variant == RFC_4122
            assert value["tail"] == 0
        first, second = observed["recovered"]
        assert UUID(first["text"]).int < UUID(second["text"]).int


@pytest.mark.parametrize(
    ("operation", "expected", "request_size"),
    [
        ("token_bytes", "20" * 8, 8),
        ("token_hex", "20" * 8, 8),
        ("token_urlsafe", "ICAgICAgICA", 8),
        ("bits", 64, 2),
        ("below", 1, 1),
        ("choice", "b", 1),
    ],
)
def test_secure_random_outage_never_uses_seeded_fallback_and_recovers(
    operation: str,
    expected: str | int,
    request_size: int,
    chaos_seed: int,
    chaos_journal: ChaosJournal,
) -> None:
    for code in ordered(chaos_seed, f"clock_entropy.secure.{operation}", FAULTS):
        observed = _run(
            "secure_entropy",
            {"operation": operation, "errno": code, "seed": chaos_seed},
            chaos_journal,
            seed=chaos_seed,
        )
        assert observed["control"] == expected
        assert observed["recovered"] == expected
        assert observed["fault_count"] == 1
        assert observed["requests"] == [request_size] * 3
        assert observed["error"] == {
            "same_instance": True,
            "type": type(OSError(code, "fault")).__name__,
            "errno": code,
        }


@pytest.mark.parametrize(
    ("operation", "expected", "recovered"),
    [
        ("token_bytes", "00ff10", "20" * 8),
        ("token_hex", "00ff10", "20" * 8),
        ("token_urlsafe", "AP8Q", "ICAgICAgICA"),
    ],
)
def test_short_entropy_retains_documented_stdlib_alias_behavior(
    operation: str,
    expected: str,
    recovered: str,
    chaos_seed: int,
    chaos_journal: ChaosJournal,
) -> None:
    observed = _run(
        "short_entropy",
        {"operation": operation},
        chaos_journal,
        seed=chaos_seed,
    )
    assert observed["requests"] == [8]
    assert observed["provider_returned"] == 3
    assert observed["value"] == expected
    assert observed["recovered"] == recovered


@pytest.mark.parametrize(
    "offsets",
    [
        (0, 0, 0),
        (0, -1000, -86400000),
        (0, 86400000, 1),
    ],
    ids=["frozen", "rollback", "jump_and_rollback"],
)
def test_wall_faults_do_not_disable_process_deadline_or_duration(
    offsets: tuple[int, ...], chaos_seed: int, chaos_journal: ChaosJournal
) -> None:
    stamps = [BASE + offset for offset in offsets]
    observed = _run(
        "clock_deadline", {"stamps": stamps}, chaos_journal, seed=chaos_seed
    )
    assert observed["epochs"] == stamps
    assert observed["wall_values"] == [stamp / 1000 for stamp in stamps]
    assert observed["clock_reads"] == [stamp * 1_000_000 for stamp in stamps]
    assert observed["wall_reads"][: len(stamps)] == [stamp / 1000 for stamp in stamps]
    assert observed["timed_out"] is True
    assert 0 <= observed["elapsed_ms"] < 10000
    assert 0 <= observed["monotonic_elapsed"] < 10
    assert 0 <= observed["real_elapsed"] < 10
    assert observed["recovered_stdout"] == "clock-recovered\n"
    assert 0 <= observed["recovered_duration"] < 5


def test_scratch_entropy_outage_leaves_existing_file_and_recovers(
    tmp_path: Path, chaos_seed: int, chaos_journal: ChaosJournal
) -> None:
    for code in ordered(chaos_seed, "clock_entropy.atomic_writer", FAULTS):
        observed = _run(
            "file_entropy",
            {"path": str(tmp_path / "document"), "errno": code},
            chaos_journal,
            seed=chaos_seed,
        )
        assert observed["error"] == {"same_instance": True, "errno": code}
        assert observed["requests"] == [4]
        assert observed["consumer_requests"] == [4]
        assert observed["failed_contents"] == "previous complete document"
        assert observed["failed_entries"] == ["document"]
        assert observed["recovered_contents"] == "new complete document"
        assert observed["recovered_entries"] == ["document"]


def test_keyed_token_authentication_survives_total_clock_and_entropy_outage(
    chaos_seed: int, chaos_journal: ChaosJournal
) -> None:
    blake3 = require_backend("blake3")
    observed = _run(
        "token_independence", {"seed": chaos_seed}, chaos_journal, seed=chaos_seed
    )
    claims = b'{"sub":"chaos-control","exp":1}'
    payload = base64.urlsafe_b64encode(claims).rstrip(b"=")
    tag = blake3.blake3(payload, key=bytes(range(32))).digest()
    expected = (payload + b"." + base64.urlsafe_b64encode(tag).rstrip(b"=")).decode()
    assert observed["before"] == expected
    assert observed["during"] == expected
    assert observed["after"] == expected
    assert observed["verified"] == claims.decode()
    assert observed["wrong_key"] is None
    assert observed["source_calls"] == []
