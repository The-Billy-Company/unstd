"""Output-checked, paired C-versus-Rust text acceleration evidence.

Run with explicit ``--baseline-native`` and ``--candidate-native`` extension
paths. Each extension gets its own copy of the unchanged Python wrappers; no
module globals change during timing. This measures the base-install wrappers,
with direct native calls reported separately. Earlier ``native-results.md``
measurements compare Python against C and are not a Rust comparison.

Samples retain every measured batch; medians and the full observed spread are
reported instead of hiding noise. Peak RSS comes from fresh subprocesses and
includes the interpreter, loaded code, inputs and allocator retention. It is an
approximate process high-water mark, not a measurement of live Rust heap bytes.
"""

from __future__ import annotations

import argparse
import builtins
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import shutil
import statistics
import struct
import subprocess
import sys
import sysconfig
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING or __package__:
    from bench.timing import _batch, _calibrate
else:
    from timing import _batch, _calibrate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_ROOT = Path(__file__).resolve().parents[1]


class StringSubclass(str):
    """A string subtype that must retain portable wrapper behavior."""


@dataclass(frozen=True)
class Case:
    """One pair, its expected dispatch and whether it stresses native storage."""

    name: str
    kernel: str
    a: Any
    b: Any
    native: bool = True
    large: bool = False


def _same(actual: Any, expected: int | float) -> bool:
    """Require exact scalar types and bit-identical floating-point results."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is float:
        return struct.pack("!d", actual) == struct.pack("!d", expected)
    return bool(actual == expected)


def _distance_reference(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Independent two-row edit DP, trimming shared ends before large probes."""
    start = 0
    limit = min(len(a), len(b))
    while start < limit and a[start] == b[start]:
        start += 1
    end_a, end_b = len(a), len(b)
    while end_a > start and end_b > start and a[end_a - 1] == b[end_b - 1]:
        end_a -= 1
        end_b -= 1
    a, b = a[start:end_a], b[start:end_b]
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        following = [i]
        for j, right in enumerate(b, 1):
            following.append(
                min(row[j] + 1, following[-1] + 1, row[j - 1] + (left != right))
            )
        row = following
    return row[-1]


def _jaro_reference(a: str, b: str) -> float:
    """Portable sliding-window Jaro without any extension imports or calls."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    taken = [False] * len(b)
    matched = []
    for i, character in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not taken[j] and character == b[j]:
                taken[j] = True
                matched.append(character)
                break
    count = len(matched)
    if not count:
        return 0.0
    ordered = [character for character, used in zip(b, taken, strict=True) if used]
    swaps = sum(x != y for x, y in zip(matched, ordered, strict=True)) // 2
    return (count / len(a) + count / len(b) + (count - swaps) / count) / 3


def _expected(case: Case, *, direct: bool = False) -> int | float:
    if case.kernel == "distance":
        return _distance_reference(case.a, case.b)
    score = _jaro_reference(case.a, case.b)
    if direct or score <= 0.7:
        return score
    prefix = 0
    for a, b in zip(case.a[:4], case.b[:4], strict=False):
        if a != b:
            break
        prefix += 1
    return min(score + prefix * 0.1 * (1.0 - score), 1.0)


def _cases() -> list[Case]:
    cases: list[Case] = []
    for name, alphabet, replacement in (
        ("ascii", "abcdefghijk", "Z"),
        ("bmp", "café東京αβ", "龍"),
        ("surrogate", "a\ud800b\udcff", "\udfff"),
        ("emoji", "🦀🚀🪼🐍", "🦋"),
    ):
        for length in (12, 64, 65, 256, 1024):
            a = (alphabet * (length // len(alphabet) + 1))[:length]
            b = a[: length // 3] + replacement + a[length // 3 + 1 : -1] + replacement
            cases.extend(
                Case(f"{kernel}/{name}{length}", kernel, a, b)
                for kernel in ("distance", "jaro")
            )
    for name, a, b in (
        ("short-name", "MARTHA", "MARHTA"),
        ("below-boost", "Jane", "John"),
        ("transposition-cycle", "ABCA", "BCAB"),
        ("single-character", "a", "b"),
        ("mixed-unicode-kind", "abcde", "abc龍e"),
    ):
        cases.append(Case(f"jaro/{name}", "jaro", a, b))
    cases.extend(
        (
            Case(
                "distance/sequence-fallback",
                "distance",
                [[1], [2], [3]],
                [[1], [4]],
                False,
            ),
            Case(
                "distance/subclass-fallback",
                "distance",
                StringSubclass("café"),
                StringSubclass("cafe"),
                False,
            ),
            Case(
                "jaro/subclass-fallback",
                "jaro",
                StringSubclass("Jon"),
                StringSubclass("John"),
                False,
            ),
        )
    )
    # At the original 4,194,304 word-step budget, with all 256 mask entries.
    alphabet = "".join(chr(0x1000 + i) for i in range(256))
    a = alphabet * 64
    cases.append(
        Case("distance/large-budget", "distance", a, a[:-1] + "龍", large=True)
    )
    # 2,048² is the original Jaro pair budget. Both inputs remain non-identical.
    a = ("abcdef東京" * 256)[:2048]
    cases.append(Case("jaro/large-budget", "jaro", a, a[:-1] + "龍", large=True))
    return cases


def _load_native(path: Path, label: str) -> Any:
    """Load exactly one explicit extension under its own initialization alias."""
    path = path.resolve()
    if not path.is_file():
        msg = f"missing native extension: {path}"
        raise FileNotFoundError(msg)
    if not any(
        path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES
    ):
        msg = f"not a native extension path: {path}"
        raise ValueError(msg)
    name = f"_unstd_text_compare_{label}._text_native"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or not isinstance(
        spec.loader, importlib.machinery.ExtensionFileLoader
    ):
        msg = f"cannot load native extension: {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if module.__file__ is None or Path(module.__file__).resolve() != path:
        msg = f"native module path mismatch: {path}"
        raise AssertionError(msg)
    for kernel in ("distance", "jaro"):
        if not inspect.isbuiltin(getattr(module, kernel, None)):
            msg = f"missing builtin native kernel: {kernel} ({path})"
            raise AssertionError(msg)
    return module


def _bundle(path: Path, source: Path, label: str) -> dict[str, Any]:
    native = _load_native(path, label)
    wrapper_path = source / "unstd" / "text" / "fuzz.py"
    if not wrapper_path.is_file():
        msg = f"missing wrapper source: {wrapper_path}"
        raise FileNotFoundError(msg)
    spec = importlib.util.spec_from_file_location(
        f"_text_compare_{label}_fuzz", wrapper_path
    )
    if spec is None or spec.loader is None:
        msg = f"cannot load wrapper source: {wrapper_path}"
        raise RuntimeError(msg)
    wrapper: Any = importlib.util.module_from_spec(spec)

    def selected_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rapidfuzz" or name.startswith("rapidfuzz."):
            msg = "comparison selects the dependency-free base-install wrappers"
            raise ImportError(msg)
        if name == "unstd.text._text_native":
            return native
        return builtins.__import__(name, *args, **kwargs)

    # Keep import selection local to this module: the other extension and any
    # installed optional backend must not affect its startup RSS or bindings.
    wrapper.__dict__["__builtins__"] = {
        **vars(builtins),
        "__import__": selected_import,
    }
    sys.modules[spec.name] = wrapper
    spec.loader.exec_module(wrapper)
    wrapper._native_distance = native.distance
    wrapper._native_jaro = native.jaro
    wrapper._HAVE_RAPIDFUZZ = False
    wrapper.distance = wrapper._levenshtein
    wrapper.jaro_winkler = wrapper._jaro_winkler
    return {"native": native, "wrapper": wrapper}


def _call(
    bundle: dict[str, Any], case: Case, *, direct: bool = False
) -> Callable[[], Any]:
    module = bundle["native"] if direct else bundle["wrapper"]
    name = (
        case.kernel
        if direct
        else {"distance": "_levenshtein", "jaro": "_jaro_winkler"}[case.kernel]
    )
    return partial(getattr(module, name), case.a, case.b)


def _validate(bundle: dict[str, Any], cases: list[Case]) -> dict[str, Any]:
    """Reject changed outputs, refused native work and hidden Python fallbacks."""
    results = {}
    for case in cases:
        expected = _expected(case)
        if not _same(_call(bundle, case)(), expected):
            msg = f"incorrect wrapper output: {case.name}"
            raise AssertionError(msg)
        direct = _call(bundle, case, direct=True)()
        if case.native:
            if direct is NotImplemented:
                msg = f"native kernel refused expected workload: {case.name}"
                raise AssertionError(msg)
            if not _same(direct, _expected(case, direct=True)):
                msg = f"incorrect native output: {case.name}"
                raise AssertionError(msg)
        elif direct is not NotImplemented:
            msg = f"native accepted expected fallback: {case.name}"
            raise AssertionError(msg)
        wrapper = bundle["wrapper"]
        attribute = f"_native_{case.kernel}"
        native = getattr(wrapper, attribute)
        outcomes: list[bool] = []

        def recorded(
            *args: Any,
            _native: Callable[..., Any] = native,
            _outcomes: list[bool] = outcomes,
        ) -> Any:
            result = _native(*args)
            _outcomes.append(result is not NotImplemented)
            return result

        try:
            setattr(wrapper, attribute, recorded)
            result = _call(bundle, case)()
        finally:
            setattr(wrapper, attribute, native)
        if not _same(result, expected) or outcomes != ([True] if case.native else []):
            msg = f"unexpected native/fallback route: {case.name}"
            raise AssertionError(msg)
        results[case.name] = {
            "route": "native" if case.native else "portable fallback",
            "native_returns": outcomes,
            "lengths": [len(case.a), len(case.b)],
            "result": expected.hex() if type(expected) is float else expected,
            "result_type": type(expected).__name__,
        }
    return results


def _timings(
    bundles: dict[str, dict[str, Any]],
    cases: list[Case],
    *,
    rounds: int,
    repetitions: int,
) -> dict[str, Any]:
    """Alternate paired batch order, retaining every elapsed nanosecond sample."""
    results = {}
    total = sum(1 + case.native for case in cases)
    gc_was_on = gc.isenabled()
    gc.disable()
    try:
        for case in cases:
            for direct in (False, True):
                if direct and not case.native:
                    continue
                calls = {
                    label: _call(bundle, case, direct=direct)
                    for label, bundle in bundles.items()
                }
                iters = max(_calibrate(call)[0] for call in calls.values())
                samples: dict[str, list[int]] = {label: [] for label in bundles}
                order = []
                for repetition in range(repetitions):
                    for round_index in range(rounds):
                        labels = list(bundles)[
                            :: 1 if (repetition * rounds + round_index) % 2 == 0 else -1
                        ]
                        order.append(labels)
                        for label in labels:
                            samples[label].append(_batch(calls[label], iters))
                medians = {
                    label: statistics.median(values) / iters
                    for label, values in samples.items()
                }
                paired = [
                    a / b
                    for a, b in zip(
                        samples["baseline"], samples["candidate"], strict=True
                    )
                ]
                row_name = f"{'native' if direct else 'wrapper'}/{case.name}"
                results[row_name] = {
                    "iterations_per_batch": iters,
                    "rounds_per_repetition": rounds,
                    "repetitions": repetitions,
                    "order": order,
                    "batch_ns": samples,
                    "median_ns": medians,
                    "baseline_over_candidate": medians["baseline"]
                    / medians["candidate"],
                    "paired_ratio_median": statistics.median(paired),
                    "paired_ratios": paired,
                    "relative_spread": {
                        label: (max(values) - min(values)) / min(values)
                        for label, values in samples.items()
                    },
                }
                if len(results) % 10 == 0 or len(results) == total:
                    print(
                        f"text native comparison: {len(results)}/{total} timing rows ({row_name})",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        if gc_was_on:
            gc.enable()
    return results


def _hash(path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _compiler(program: str) -> str | None:
    executable = shutil.which(program)
    if executable is None:
        return None
    result = subprocess.run(  # noqa: S603
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return (
        (result.stdout or result.stderr).splitlines()[0]
        if result.returncode == 0
        else None
    )


def _rss_bytes() -> int:
    # resource is intentionally imported only for this optional POSIX probe.
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _memory_worker(path: Path, source: Path, case_name: str) -> dict[str, Any]:
    bundle = _bundle(path, source, "memory")
    case = next(case for case in _cases() if case.name == case_name and case.large)
    call = _call(bundle, case)
    gc.collect()
    before = _rss_bytes()
    result = None
    for _ in range(8):
        result = call()
    peak = _rss_bytes()
    # Parent validation already checked both implementations. Keep the worker's
    # independent oracle allocations after the saved memory high-water mark.
    if not _same(result, _expected(case)):
        msg = f"incorrect output in memory probe: {case.name}"
        raise AssertionError(msg)
    return {
        "peak_rss_bytes": peak,
        "peak_before_calls_bytes": before,
        "peak_increase_bytes": max(0, peak - before),
        "calls": 8,
    }


def _memory(
    bundles: dict[str, Path], source: Path, cases: list[Case], repetitions: int
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for case in cases:
        if not case.large:
            continue
        samples: dict[str, list[Any]] = {label: [] for label in bundles}
        for repetition in range(repetitions):
            for label in list(bundles)[:: 1 if repetition % 2 == 0 else -1]:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--memory-worker",
                    str(bundles[label]),
                    "--source",
                    str(source),
                    "--memory-case",
                    case.name,
                ]
                result = subprocess.run(  # noqa: S603
                    command, check=True, capture_output=True, text=True, timeout=120
                )
                samples[label].append(json.loads(result.stdout))
        results[case.name] = {
            "samples": samples,
            "median_peak_rss_bytes": {
                label: statistics.median(row["peak_rss_bytes"] for row in values)
                for label, values in samples.items()
            },
            "median_peak_increase_bytes": {
                label: statistics.median(row["peak_increase_bytes"] for row in values)
                for label, values in samples.items()
            },
        }
    return results


def main() -> None:
    """Validate both explicit native paths before producing paired evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-native", type=Path)
    parser.add_argument("--candidate-native", type=Path)
    parser.add_argument("--baseline-source", type=Path, action="append", default=[])
    parser.add_argument("--candidate-source", type=Path, action="append", default=[])
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--memory-repetitions", type=int, default=3)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--memory-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--memory-case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    if args.memory_worker is not None:
        if args.memory_case is None:
            parser.error("memory worker requires memory case")
        print(json.dumps(_memory_worker(args.memory_worker, source, args.memory_case)))
        return
    if args.baseline_native is None or args.candidate_native is None:
        parser.error("both --baseline-native and --candidate-native are required")
    if min(args.rounds, args.repetitions, args.memory_repetitions) < 1:
        parser.error("rounds and repetitions must be positive")
    paths = {
        "baseline": args.baseline_native.resolve(),
        "candidate": args.candidate_native.resolve(),
    }
    if paths["baseline"] == paths["candidate"]:
        parser.error("baseline and candidate must be separate extension files")
    bundles = {label: _bundle(path, source, label) for label, path in paths.items()}
    cases = _cases()
    checked = {label: _validate(bundle, cases) for label, bundle in bundles.items()}
    if checked["baseline"] != checked["candidate"]:
        msg = "baseline and candidate outputs or routes differ"
        raise AssertionError(msg)
    output = {
        "comparison": "original C versus candidate native text extension",
        "measured_api": "base-install fuzz._levenshtein / fuzz._jaro_winkler; native calls secondary",
        "estimator": "median of all paired batch means; calibration excluded",
        "noise_note": "relative_spread is (slowest-fastest)/fastest; shared-host timings are not a universal performance guarantee",
        "prior_results_note": "bench/native-results.md compares Python versus C, not C versus Rust",
        "utc": datetime.now(UTC).isoformat(),
        "harness": _hash(Path(__file__)),
        "wrapper": _hash(source / "unstd" / "text" / "fuzz.py"),
        "native": {label: _hash(path) for label, path in paths.items()},
        "sources": {
            "baseline": [_hash(path) for path in args.baseline_source],
            "candidate": [_hash(path) for path in args.candidate_source],
        },
        "toolchain_note": "Compiler versions describe the current tools; use recorded build commands to establish the flags used for each saved binary.",
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_compiler": platform.python_compiler(),
            "rustc": _compiler("rustc"),
            "cc": _compiler("cc"),
            "python_build": {
                name: sysconfig.get_config_var(name)
                for name in ("CC", "CFLAGS", "LDSHARED")
            },
        },
        "validation": checked,
        "timings": {}
        if args.check_only
        else _timings(bundles, cases, rounds=args.rounds, repetitions=args.repetitions),
        "memory_note": "Fresh subprocess peak RSS includes Python, code, inputs, and allocator retention; peak increase may be zero when startup already set the high-water mark. This is not live native heap usage.",
        "memory": {}
        if args.no_memory or args.check_only or sys.platform == "win32"
        else _memory(paths, source, cases, args.memory_repetitions),
    }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
