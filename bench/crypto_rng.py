"""Compare complete secure-token generation with optional SIMD Base64 formatting.

This experiment never changes the production secrets aliases. A temporary
stdlib entropy hook checks exact known-byte results outside measurements; timed
generation uses the original OS CSPRNG. Codec-only cases isolate the formatting
opportunity rather than presenting it as complete-generation throughput.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from functools import partial
import gc
import hashlib
from importlib.metadata import version
import inspect
import json
import math
import os
from pathlib import Path
import platform
import random
import secrets
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pybase64

from unstd.rand import crypto


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class Workload:
    """Generation or formatting over one input size, prepared before timing."""

    name: str
    input_bytes: int
    before: Callable[[], str]
    after: Callable[[], str]


def _candidate(nbytes: int | None = None) -> str:
    return (
        pybase64.urlsafe_b64encode(secrets.token_bytes(nbytes))
        .rstrip(b"=")
        .decode("ascii")
    )


def _stdlib_format(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _native_format(data: bytes) -> str:
    return pybase64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _outcome(call: Callable[[], str]) -> tuple[str, str]:
    try:
        result = call()
    except Exception as error:
        return type(error).__name__, str(error)
    return type(result).__name__, result


def _contracts() -> dict[str, Any]:
    sizes = (0, 1, 2, 3, 32, 256, 4096, 65536, 1 << 20)
    requests: list[int] = []

    def entropy(size: int) -> bytes:
        requests.append(size)
        return (bytes(range(256)) * ((size + 255) // 256))[:size]

    checks = []
    with patch.object(random, "_urandom", entropy):
        for size in sizes:
            expected = _stdlib_format(entropy(size))
            requests.clear()
            assert secrets.token_urlsafe(size) == expected
            assert _candidate(size) == expected
            assert requests == [size, size]
            checks.append({"input_bytes": size, "output_chars": len(expected)})
        assert secrets.token_urlsafe() == _candidate()
        assert secrets.token_urlsafe(None) == _candidate(None)
    inputs: tuple[Any, ...] = (-1, 1.25, "32", object(), 1 << 100, True, False)
    refusals = []
    for argument in inputs:
        before = _outcome(partial(secrets.token_urlsafe, argument))
        after = _outcome(partial(_candidate, argument))
        # Valid bool inputs still contain OS randomness. Their shape, rather
        # than their independent random values, is the comparison contract.
        if type(argument) is bool:
            assert before[0] == after[0] == "str"
            assert len(before[1]) == len(after[1])
        else:
            assert before == after
        refusals.append(
            {"argument_type": type(argument).__name__, "before": before, "after": after}
        )
    index_observations = []

    class Indexed:
        def __init__(self, value: int, observations: list[str]) -> None:
            self.value = value
            self.observations = observations

        def __index__(self) -> int:
            self.observations.append("index")
            return self.value

    for indexed in (0, 16, -1):
        calls: list[str] = []
        argument = Indexed(indexed, calls)
        # Only correctness probes replace entropy. The real syscall retains
        # responsibility for __index__ conversion and exact refusal behavior.
        before = _outcome(partial(secrets.token_urlsafe, argument))  # type: ignore[arg-type]
        before_calls = calls.copy()
        calls.clear()
        after = _outcome(partial(_candidate, argument))  # type: ignore[arg-type]
        assert calls == before_calls == ["index"]
        if indexed >= 0:
            assert before[0] == after[0] == "str"
            assert len(before[1]) == len(after[1])
        else:
            assert before == after
        index_observations.append(
            {"index": indexed, "calls": calls, "result_type": before[0]}
        )
    failure = OSError("fixture entropy unavailable")
    with patch.object(random, "_urandom", side_effect=failure):
        for call in (secrets.token_urlsafe, _candidate):
            try:
                call(32)
            except OSError as error:
                if error is not failure:
                    msg = "entropy failure identity changed"
                    raise AssertionError(msg) from error
            else:
                msg = "entropy failure was swallowed"
                raise AssertionError(msg)
    return {
        "known_byte_checks": checks,
        "default_and_none_match": True,
        "invalid_argument_outcomes": refusals,
        "index_contracts": index_observations,
        "entropy_exception_identity_preserved": True,
        "production_alias_identity": crypto.token_urlsafe is secrets.token_urlsafe,
        "candidate_is_exact_alias": _candidate is secrets.token_urlsafe,
        "production_signature": str(inspect.signature(crypto.token_urlsafe)),
        "candidate_signature": str(inspect.signature(_candidate)),
    }


def _batch(call: Callable[[], str], iterations: int) -> dict[str, int | float]:
    cpu = time.process_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(iterations):
        call()
    return {
        "iterations": iterations,
        "wall_ns": (time.perf_counter_ns() - wall) / iterations,
        "cpu_ns": (time.process_time_ns() - cpu) / iterations,
    }


def _calibrate(call: Callable[[], str], target_ns: int) -> int:
    iterations = 1
    while True:
        elapsed = float(_batch(call, iterations)["wall_ns"]) * iterations
        if elapsed >= target_ns / 2:
            return max(1, round(iterations * target_ns / elapsed))
        iterations *= max(2, min(32, round(target_ns / max(1, elapsed))))


def _interval(ratios: list[float]) -> list[float]:
    # Statistical resampling uses an isolated deterministic PRNG, never the
    # production entropy provider or the process-global random state.
    rng = random.Random(29)
    samples = sorted(
        statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(1999)
    )
    return [samples[49], samples[1949]]


def _measure(case: Workload, rounds: int, seconds: float, index: int) -> dict[str, Any]:
    calls = {"before": case.before, "after": case.after}
    iterations = {
        side: _calibrate(call, round(seconds * 1e9)) for side, call in calls.items()
    }
    samples: dict[str, list[dict[str, int | float]]] = {side: [] for side in calls}
    order = []
    enabled = gc.isenabled()
    gc.disable()
    try:
        for at in range(rounds):
            sides = list(calls)[:: 1 if (index + at) % 2 == 0 else -1]
            order.append(sides)
            for side in sides:
                samples[side].append(_batch(calls[side], iterations[side]))
    finally:
        if enabled:
            gc.enable()
    metrics = {}
    for metric in ("wall_ns", "cpu_ns"):
        old, new = [
            [float(sample[metric]) for sample in samples[side]]
            for side in ("before", "after")
        ]
        ratios = [left / right for left, right in zip(old, new, strict=True)]
        metrics[metric] = {
            "median_before": statistics.median(old),
            "median_after": statistics.median(new),
            "paired_median_speedup": statistics.median(ratios),
            "bootstrap_95_percent_interval": _interval(ratios),
            "paired_ratios": ratios,
            "noisy": max(old) / min(old) > 1.25 or max(new) / min(new) > 1.25,
        }
    return {
        "input_bytes": case.input_bytes,
        "samples": samples,
        "order": order,
        "metrics": metrics,
    }


def _hashes() -> dict[str, str]:
    files = [
        Path(__file__).resolve(),
        Path(crypto.__file__).resolve(),
        Path(secrets.__file__).resolve(),
        Path(random.__file__).resolve(),
        Path(base64.__file__).resolve(),
        Path(pybase64.__file__).resolve(),
        Path(sys.executable).resolve(),
    ]
    files.extend(Path(pybase64.__file__).parent.glob("*.so"))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def _load_average() -> tuple[float, float, float] | None:
    """Collect optional host metadata outside all measurement timers."""
    if sys.platform == "win32":
        return None
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None


def main() -> None:
    """Check semantics and preserve complete generation and codec-only samples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=11)
    parser.add_argument("--seconds", type=float, default=0.01)
    args = parser.parse_args()
    if args.rounds < 1 or not math.isfinite(args.seconds) or args.seconds < 0.001:
        parser.error("rounds must be positive and seconds must be finite and >=0.001")
    original_entropy = vars(random)["_urandom"]
    contracts = _contracts()
    assert vars(random)["_urandom"] is original_entropy is os.urandom
    assert crypto.token_urlsafe is secrets.token_urlsafe
    hashes = _hashes()
    sizes = (0, 32, 256, 4096, 65536, 1 << 20)
    cases = [
        Workload(
            "generation/default",
            secrets.DEFAULT_ENTROPY,
            secrets.token_urlsafe,
            _candidate,
        ),
        Workload(
            "generation/None",
            secrets.DEFAULT_ENTROPY,
            partial(secrets.token_urlsafe, None),
            partial(_candidate, None),
        ),
    ]
    for size in sizes:
        cases.append(
            Workload(
                f"generation/{size}",
                size,
                partial(secrets.token_urlsafe, size),
                partial(_candidate, size),
            )
        )
        data = (bytes(range(256)) * ((size + 255) // 256))[:size]
        assert _stdlib_format(data) == _native_format(data)
        cases.append(
            Workload(
                f"format-only/{size}",
                size,
                partial(_stdlib_format, data),
                partial(_native_format, data),
            )
        )
    cases.append(
        Workload(
            "generation/self-control/32",
            32,
            partial(secrets.token_urlsafe, 32),
            partial(secrets.token_urlsafe, 32),
        )
    )
    results = {}
    for index, case in enumerate(cases):
        result = _measure(case, args.rounds, args.seconds, index)
        results[case.name] = result
        print(
            f"{case.name}: {result['metrics']['wall_ns']['paired_median_speedup']:.3f}x wall; {result['metrics']['cpu_ns']['paired_median_speedup']:.3f}x CPU",
            flush=True,
        )
    assert hashes == _hashes()
    assert vars(random)["_urandom"] is original_entropy
    assert crypto.token_urlsafe is secrets.token_urlsafe
    report = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "load_average": _load_average(),
            "pybase64_version": version("pybase64"),
        },
        "methodology": {
            "rounds": args.rounds,
            "target_batch_seconds": args.seconds,
            "entropy_hook_active_during_timing": False,
            "generation_scope": "Full OS-backed token_bytes call plus Base64 formatting, padding removal, and ASCII decoding; each arm draws independently.",
            "format_only_scope": "Prepared identical immutable bytes; excludes OS entropy generation.",
            "bootstrap": "Descriptive paired-median resampling:1999samples,seed29; no all-platform guarantee.",
        },
        "contracts": contracts,
        "source_sha256": hashes,
        "cases": results,
        "harness_source": Path(__file__).read_text(),
        "decision": "Experimental only. Production remains the exact secrets.token_urlsafe alias; substitution would break identity and require an optional codec on the stdlib-only CSPRNG path.",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
