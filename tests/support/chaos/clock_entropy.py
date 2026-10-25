"""Process-isolated clock and entropy experiments, with JSON observations.

Only this short-lived worker patches process-global clocks and entropy. Fault
providers count calls and fail once; recovery uses fixed bytes, so no assertion
depends on collision probabilities or on the quality of a CSPRNG.
"""

from __future__ import annotations

from itertools import pairwise
import json
from pathlib import Path
import random
import sys
import time
from unittest.mock import patch


BASE_MS = 1_700_000_000_000


def _uuid_fields(value: object) -> dict[str, object]:
    number = value.int
    return {
        "text": str(value),
        "timestamp": number >> 80,
        "counter": (number >> 64) & 0xFFF,
        "tail": number & ((1 << 62) - 1),
        "version": value.version,
        "variant": value.variant,
    }


def _uuid_clock(config: dict[str, object]) -> dict[str, object]:
    from unstd.ids import uid

    pattern = config["stamps"]
    stamps = [pattern[index % len(pattern)] for index in range(config["count"])]
    counter_seed = config["counter_seed"]
    clock = iter(stamps)
    entropy_sizes = []

    def entropy(size: int) -> bytes:
        entropy_sizes.append(size)
        return counter_seed.to_bytes(2, "big") if size == 2 else b" " * size

    uid._last_ms, uid._last_counter = None, 0
    with (
        patch.object(uid.time, "time_ns", lambda: next(clock) * 1_000_000),
        patch.object(uid.os, "urandom", entropy),
    ):
        # Route through the public API while forcing the always-defined fallback.
        with patch.object(uid, "_uuid7", uid._stdlib_uuid7):
            values = [uid.parse(uid.new()) for _ in stamps]
        last = uid._last_ms, uid._last_counter
    checkpoints = sorted({0, 1, len(values) // 2, len(values) - 2, len(values) - 1})
    return {
        "count": len(values),
        "stamps": pattern,
        "checkpoints": {str(i): _uuid_fields(values[i]) for i in checkpoints},
        "strictly_increasing": all(a.int < b.int for a, b in pairwise(values)),
        "unique": len({value.int for value in values}) == len(values),
        "seed_requests": entropy_sizes.count(2),
        "tail_requests": entropy_sizes.count(8),
        "other_requests": sorted(set(entropy_sizes) - {2, 8}),
        "final_state": last,
    }


def _fault(code: int) -> OSError:
    return OSError(code, "injected entropy provider failure")


def _uuid_entropy(config: dict[str, object]) -> dict[str, object]:
    from unstd.ids import uid

    phase = config["phase"]
    clock = BASE_MS
    entropy_sizes = []
    error = _fault(config["errno"])
    armed = False
    fault_count = 0

    def entropy(size: int) -> bytes:
        nonlocal armed, fault_count
        entropy_sizes.append(size)
        target_size = 8 if phase.endswith("tail") else 2
        if armed and size == target_size:
            armed = False
            fault_count += 1
            raise error
        return b"\x00" * size

    uid._last_ms, uid._last_counter = None, 0
    with (
        patch.object(uid.time, "time_ns", lambda: clock * 1_000_000),
        patch.object(uid.os, "urandom", entropy),
        patch.object(uid, "_uuid7", uid._stdlib_uuid7),
    ):
        before = None if phase == "initial_seed" else uid.parse(uid.new())
        if phase in {"advanced_seed", "advanced_tail"}:
            clock += 1
        elif phase in {"overflow_seed", "overflow_tail"}:
            uid._last_counter = 0xFFF
        state_before = uid._last_ms, uid._last_counter
        armed = True
        try:
            uid.new()
        except OSError as caught:
            error_observed = {
                "same_instance": caught is error,
                "type": type(caught).__name__,
                "errno": caught.errno,
            }
        else:
            error_observed = None
        state_failed = uid._last_ms, uid._last_counter
        recovered = [uid.parse(uid.new()), uid.parse(uid.new())]
    return {
        "before": _uuid_fields(before) if before is not None else None,
        "error": error_observed,
        "state_before": state_before,
        "state_failed": state_failed,
        "recovered": [_uuid_fields(value) for value in recovered],
        "requests": entropy_sizes,
        "fault_count": fault_count,
        "increasing_after_fault": recovered[0].int < recovered[1].int
        and (before is None or before.int < recovered[0].int),
    }


def _secure_entropy(config: dict[str, object]) -> dict[str, object]:
    from unstd import rand
    from unstd.rand import crypto

    operation = config["operation"]
    args = {
        "token_bytes": (8,),
        "token_hex": (8,),
        "token_urlsafe": (8,),
        "bits": (9,),
        "below": (7,),
        "choice": ("abcd",),
    }[operation]
    entropy_sizes = []
    error = _fault(config["errno"])
    fault_count = 0
    armed = False

    def entropy(size: int) -> bytes:
        nonlocal armed, fault_count
        entropy_sizes.append(size)
        if armed:
            armed = False
            fault_count += 1
            raise error
        return b" " * size

    call = getattr(crypto, operation)
    with patch.object(random, "_urandom", entropy):
        control = call(*args)
        rand.seed(config["seed"])
        armed = True
        try:
            call(*args)
        except OSError as caught:
            error_observed = {
                "same_instance": caught is error,
                "type": type(caught).__name__,
                "errno": caught.errno,
            }
        else:
            error_observed = None
        recovered = call(*args)
    return {
        "control": control.hex() if isinstance(control, bytes) else control,
        "recovered": recovered.hex() if isinstance(recovered, bytes) else recovered,
        "error": error_observed,
        "fault_count": fault_count,
        "requests": entropy_sizes,
    }


def _short_entropy(config: dict[str, object]) -> dict[str, object]:
    from unstd.rand import crypto

    operation = config["operation"]
    requests = []

    def short(size: int) -> bytes:
        requests.append(size)
        return b"\x00\xff\x10"

    # This violates os.urandom's exact-length provider contract deliberately.
    # These aliases promise stdlib behavior, not additional provider validation.
    with patch.object(random, "_urandom", short):
        value = getattr(crypto, operation)(8)
    with patch.object(random, "_urandom", lambda size: b" " * size):
        recovered = getattr(crypto, operation)(8)
    return {
        "value": value.hex() if isinstance(value, bytes) else value,
        "recovered": recovered.hex() if isinstance(recovered, bytes) else recovered,
        "requests": requests,
        "provider_returned": 3,
    }


def _clock_deadline(config: dict[str, object]) -> dict[str, object]:
    from unstd import proc
    from unstd.time import timeutil

    stamps = config["stamps"]
    reads = []
    observed_time_ns = []

    def time_ns() -> int:
        value = stamps[len(observed_time_ns) % len(stamps)] * 1_000_000
        observed_time_ns.append(value)
        return value

    def wall() -> float:
        value = stamps[len(reads) % len(stamps)] / 1000
        reads.append(value)
        return value

    started = time.monotonic()
    with (
        patch.object(time, "time", wall),
        patch.object(time, "time_ns", time_ns),
        patch.object(timeutil, "wall", wall),
    ):
        epochs = [timeutil.epoch_ms() for _ in stamps]
        wall_values = [timeutil.wall() for _ in stamps]
        mono_start = timeutil.mono()
        perf_start = timeutil.perf()
        try:
            proc.run(
                # Even a broken deadline leaves a child bounded by two seconds.
                [sys.executable, "-I", "-c", "import time; time.sleep(2)"],
                timeout=0.1,
            )
        except proc.TimeoutExpired:
            timed_out = True
        else:
            timed_out = False
        elapsed_ms = timeutil.elapsed_ms(perf_start)
        monotonic_elapsed = timeutil.mono() - mono_start
        result = proc.run(
            [sys.executable, "-I", "-c", "print('clock-recovered')"], timeout=5
        )
    return {
        "epochs": epochs,
        "wall_values": wall_values,
        "wall_reads": reads,
        "clock_reads": observed_time_ns,
        "timed_out": timed_out,
        "elapsed_ms": elapsed_ms,
        "monotonic_elapsed": monotonic_elapsed,
        "real_elapsed": time.monotonic() - started,
        "recovered_stdout": result.stdout,
        "recovered_duration": result.duration,
    }


def _file_entropy(config: dict[str, object]) -> dict[str, object]:
    from unstd import fs

    destination = Path(config["path"])
    destination.write_bytes(b"previous complete document")
    error = _fault(config["errno"])
    requests = []
    consumer_requests = []
    token_hex = fs.secrets.token_hex

    def entropy(size: int) -> bytes:
        requests.append(size)
        raise error

    def scratch_token(size: int) -> str:
        consumer_requests.append(size)
        return token_hex(size)

    # This scenario injects Python's provider, so expose that consumer binding.
    # Linux's default native getrandom path is faulted separately at its syscall.
    with (
        patch.object(random, "_urandom", entropy),
        patch.object(fs.secrets, "token_hex", scratch_token),
    ):
        try:
            fs.atomic_write(destination, b"new complete document")
        except OSError as caught:
            error_observed = {
                "same_instance": caught is error,
                "errno": caught.errno,
            }
        else:
            error_observed = None
        failed_contents = destination.read_bytes()
        failed_entries = sorted(path.name for path in destination.parent.iterdir())
    fs.atomic_write(destination, b"new complete document")
    return {
        "error": error_observed,
        "requests": requests,
        "consumer_requests": consumer_requests,
        "failed_contents": failed_contents.decode("ascii"),
        "failed_entries": failed_entries,
        "recovered_contents": destination.read_text(),
        "recovered_entries": sorted(path.name for path in destination.parent.iterdir()),
    }


def _token_independence(config: dict[str, object]) -> dict[str, object]:
    from unstd.crypto import token

    key = bytes(range(32))
    claims = b'{"sub":"chaos-control","exp":1}'
    source_calls = []

    def unavailable(*args: object) -> bytes:
        source_calls.append(args)
        message = "MAC operations unexpectedly requested clock or entropy"
        raise AssertionError(message)

    before = token.mint(key, claims)
    with (
        patch.object(random, "_urandom", unavailable),
        patch.object(time, "time", unavailable),
        patch.object(time, "time_ns", unavailable),
    ):
        during = token.mint(key, claims)
        verified = token.verify(key, during)
        rejected = token.verify(bytes(reversed(key)), during)
    after = token.mint(key, claims)
    return {
        "before": before,
        "during": during,
        "after": after,
        "verified": verified.decode("ascii") if verified is not None else None,
        "wrong_key": rejected,
        "source_calls": source_calls,
        "seed": config["seed"],
    }


_SCENARIOS = {
    "uuid_clock": _uuid_clock,
    "uuid_entropy": _uuid_entropy,
    "secure_entropy": _secure_entropy,
    "short_entropy": _short_entropy,
    "clock_deadline": _clock_deadline,
    "file_entropy": _file_entropy,
    "token_independence": _token_independence,
}


def main() -> None:
    """Execute one reviewed scenario and emit only its JSON observations."""
    from tests.support.chaos.journal import apply_backend_policy

    apply_backend_policy()
    scenario = sys.argv[1]
    config = json.loads(sys.argv[2])
    observed = _SCENARIOS[scenario](config)
    print(json.dumps(observed, sort_keys=True))


if __name__ == "__main__":
    main()
