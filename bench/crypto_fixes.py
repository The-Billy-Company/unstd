"""Paired public-call checks for crypto hardening, with CPU and wall evidence.

The saved source contains unstd/crypto. All workloads compare identical values
and types before timing. The intentional default token-size restriction is
tested separately, not presented as a speedup over accepting a large token.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
import hashlib
from importlib import util
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import random
import ssl
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any

from bench.crypto_hash import _calibrate, _load, _load_average, _source_hashes
from unstd.crypto import digest, token


if TYPE_CHECKING:
    from collections.abc import Callable


_KEY = bytes(range(32))
_OTHER_KEY = bytes(reversed(range(32)))


@dataclass(frozen=True, slots=True)
class Workload:
    """Public callables over preallocated inputs, with matching output contracts."""

    name: str
    before: Callable[[], Any]
    after: Callable[[], Any]
    note: str


def _cases(before: Any, *, verifier_route: str = "auto") -> list[Workload]:
    verify = token._verify_python if verifier_route == "python" else token.verify
    prepared = (
        {limit: token.prepare_verifier(max_token_chars=limit) for limit in (8192, None)}
        if verifier_route == "prepared"
        else {}
    )

    def candidate(key: bytes, value: str, *, bulk: bool = False) -> Callable[[], Any]:
        if prepared:
            return partial(prepared[None if bulk else 8192], key, value)
        return partial(
            verify, key, value, **({"max_token_chars": None} if bulk else {})
        )

    cases = []
    for size in (1, 42, 256, 1024, 4096, 6111, 65536):
        claims = bytes(at % 251 for at in range(size))
        bearer = before.mint(_KEY, claims)
        assert token.mint(_KEY, claims) == bearer
        forged = bearer[:-2] + ("A" if bearer[-2] != "A" else "B") + bearer[-1]
        options = {"max_token_chars": None} if len(bearer) > 8192 else {}
        for name, key, value in (
            ("authentic", _KEY, bearer),
            ("forged", _KEY, forged),
            ("wrong-key", _OTHER_KEY, bearer),
        ):
            cases.append(
                Workload(
                    f"verify/{size}/{name}",
                    partial(before.verify, key, value),
                    candidate(key, value, bulk=bool(options)),
                    "Explicit unbounded opt-in for bulk only"
                    if options
                    else "Default bounded public verifier",
                )
            )
    good = before.mint(_KEY, b"x" * 42)
    payload, tag = good.split(".")
    for name, value in (
        ("empty", ""),
        ("no-separator", "junk"),
        ("tiny", "a.b"),
        ("extra-separator", good + ".extra"),
        ("short-tag", payload + "." + tag[:-1]),
        ("unicode", "é" + payload + "." + tag),
        ("padding", payload + "=." + tag),
        ("invalid-payload-length", "a." + tag),
        ("invalid-pad-bits", "Zh." + tag),
    ):
        cases.append(
            Workload(
                f"verify/malformed/{name}",
                partial(before.verify, _KEY, value),
                candidate(_KEY, value),
                "Same rejection result; includes cheapest old reject paths",
            )
        )
    cases.extend(
        [
            Workload(
                "mint/control",
                partial(before.mint, _KEY, b"x" * 42),
                partial(token.mint, _KEY, b"x" * 42),
                "Unchanged implementation control",
            ),
            Workload(
                "mac/control",
                partial(before.mac_raw, _KEY, b"x" * 64),
                partial(token.mac_raw, _KEY, b"x" * 64),
                "Unchanged implementation control",
            ),
            Workload(
                "self/control",
                partial(before.verify, _KEY, good),
                partial(before.verify, _KEY, good),
                "Exact same callable implementation on both sides",
            ),
        ]
    )
    return cases


def _concurrent(executor: ThreadPoolExecutor, call: Callable[[], bytes]) -> list[bytes]:
    """Four independent public calls on an already-running application executor."""
    futures = [executor.submit(call) for _ in range(4)]
    return [future.result() for future in futures]


def _pool_cases(source: Path, stack: ExitStack) -> list[Workload]:
    candidates = {}
    for cap in (1, 2, 4, 8):
        module, _ = _load(source)
        sys.modules[module.__package__ + "._blake3"].__dict__["_PARALLEL_THREADS"] = cap
        candidates[cap] = module
    executor = stack.enter_context(ThreadPoolExecutor(max_workers=4))
    cases = []
    for size in (1 << 20, 4 << 20, 16 << 20, 64 << 20):
        data = b"x" * size
        for callers in (1, 4):
            for cap, module in candidates.items():
                old, new = partial(digest.raw, data), partial(module.raw, data)
                cases.append(
                    Workload(
                        f"raw/{size}/callers-{callers}/cap-{cap}",
                        old if callers == 1 else partial(_concurrent, executor, old),
                        new if callers == 1 else partial(_concurrent, executor, new),
                        f"Current adaptive 4/8 workers versus cap {cap}; {callers * size} total input bytes; application executor setup excluded, submissions included equally",
                    )
                )
    return cases


def _batch(call: Callable[[], Any], iterations: int) -> dict[str, float | int]:
    start_wall, start_cpu, start_thread = (
        time.perf_counter_ns(),
        time.process_time_ns(),
        time.thread_time_ns(),
    )
    for _ in range(iterations):
        call()
    thread, cpu, wall = (
        time.thread_time_ns() - start_thread,
        time.process_time_ns() - start_cpu,
        time.perf_counter_ns() - start_wall,
    )
    return {
        "iterations": iterations,
        "wall_ns": wall / iterations,
        "cpu_ns": cpu / iterations,
        "thread_ns": thread / iterations,
    }


def _interval(samples: list[float]) -> list[float]:
    # Deterministic resampling is benchmark statistics, never security entropy.
    rng = random.Random(173)
    medians = sorted(
        statistics.median(rng.choices(samples, k=len(samples))) for _ in range(1999)
    )
    return [medians[49], medians[1949]]


def _measure(case: Workload, rounds: int, seconds: float, index: int) -> dict[str, Any]:
    expected, actual = case.before(), case.after()
    if type(expected) is not type(actual) or expected != actual:
        msg = f"different outputs: {case.name}"
        raise AssertionError(msg)
    calls = {"before": case.before, "after": case.after}
    iterations = {
        label: _calibrate(call, round(seconds * 1e9)) for label, call in calls.items()
    }
    samples: dict[str, list[dict[str, float | int]]] = {label: [] for label in calls}
    enabled = gc.isenabled()
    gc.disable()
    try:
        for at in range(rounds):
            for label in ("before", "after")[:: 1 if (at + index) % 2 == 0 else -1]:
                samples[label].append(_batch(calls[label], iterations[label]))
    finally:
        if enabled:
            gc.enable()
    result: dict[str, Any] = {"note": case.note, "samples": samples, "metrics": {}}
    for metric in ("wall_ns", "cpu_ns", "thread_ns"):
        old, new = [
            [float(row[metric]) for row in samples[label]]
            for label in ("before", "after")
        ]
        ratios = [left / right for left, right in zip(old, new, strict=True)]
        result["metrics"][metric] = {
            "median_before": statistics.median(old),
            "median_after": statistics.median(new),
            "paired_median_speedup": statistics.median(ratios),
            "bootstrap_95_percent_interval": _interval(ratios),
            "paired_ratios": ratios,
            "noisy": max(old) / min(old) > 1.25 or max(new) / min(new) > 1.25,
        }
    return result


def main() -> None:
    """Validate old/current calls and record all diagnostic observations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=19)
    parser.add_argument("--seconds", type=float, default=0.015)
    parser.add_argument("--mode", choices=("tokens", "pools"), default="tokens")
    parser.add_argument("--native-token", type=Path)
    parser.add_argument(
        "--token-path",
        dest="verifier_route",
        choices=("auto", "python", "prepared"),
        default="auto",
    )
    args = parser.parse_args()
    if args.native_token and args.verifier_route != "auto":
        parser.error("isolated native experiments require --token-path=auto")
    if (
        args.rounds < 3
        or not math.isfinite(args.seconds)
        or not 0.001 <= args.seconds <= 1
    ):
        parser.error("at least three rounds and finite .001..1 seconds are required")
    source = Path(digest.__file__).resolve().parents[2]
    before_digest, before_token = _load(args.compare_source.resolve())
    assert before_digest.raw(b"control") == digest.raw(b"control")
    native_metadata = None
    if args.native_token:
        native_path = args.native_token.resolve()
        spec = util.spec_from_file_location("_token_experiment", native_path)
        if spec is None or spec.loader is None:
            msg = "cannot load isolated native token experiment"
            raise RuntimeError(msg)
        module: Any = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        implementation = module.bind(token.verify)

        def native_verify(
            key: bytes, token: str, *, max_token_chars: int | None = 8192
        ) -> bytes | None:
            return implementation(key, token, max_token_chars)  # type: ignore[no-any-return]

        token.verify = native_verify
        native_metadata = {
            "path": str(native_path),
            "sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
            "source": native_path.with_name("_token_experiment.c").read_text(),
        }
    harness = Path(__file__).read_bytes()
    report: dict[str, Any] = {
        "schema": 1,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "openssl": ssl.OPENSSL_VERSION,
        "blake3": version("blake3"),
        "pybase64": version("pybase64"),
        "cpu_count": os.cpu_count(),
        "load_start": _load_average(),
        "source_hashes": {
            "before": _source_hashes(args.compare_source.resolve()),
            "after": _source_hashes(source),
        },
        "before_token_source": (
            args.compare_source / "unstd/crypto/token.py"
        ).read_text(),
        "after_token_source": Path(token.__file__).read_text(),
        "harness_sha256": hashlib.sha256(harness).hexdigest(),
        "measured_harness_source": harness.decode(),
        "rounds": args.rounds,
        "seconds": args.seconds,
        "mode": args.mode,
        "native_token_experiment": native_metadata,
        "verifier_route": args.verifier_route,
        "token_verify_implementation": token.verify.__code__.co_name,
        "prepared_setup": "Excluded explicitly; factory called once per policy before timing"
        if args.verifier_route == "prepared"
        else None,
        "ratio": "before time / after time; >1 favors after",
        "limitations": "Batched observations on a shared host; bootstrap intervals are exploratory, not independent-call tail-latency or portable no-regression certification.",
        "cases": {},
    }
    with ExitStack() as stack:
        cases = (
            _cases(before_token, verifier_route=args.verifier_route)
            if args.mode == "tokens"
            else _pool_cases(source, stack)
        )
        for at, case in enumerate(cases):
            row = _measure(case, args.rounds, args.seconds, at)
            report["cases"][case.name] = row
            wall, cpu = row["metrics"]["wall_ns"], row["metrics"]["cpu_ns"]
            print(
                f"{case.name:45} wall {wall['paired_median_speedup']:5.2f}x CPU {cpu['paired_median_speedup']:5.2f}x",
                flush=True,
            )
    report["load_end"] = _load_average()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
