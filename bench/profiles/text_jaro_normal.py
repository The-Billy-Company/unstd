"""Measure ordinary public Jaro matching with installed and base backends.

The fixed contact directory and committed module paths contain short strings.
Every complete API result must equal an exhaustive scalar ranking or matrix.
Correctness, dispatch observation, and calibration remain outside paired samples.
"""

from __future__ import annotations

import argparse
import builtins
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
import hashlib
from importlib.machinery import EXTENSION_SUFFIXES
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from typing import TYPE_CHECKING, Any, Literal


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.core.timing import _calibrate
from bench.profiles.text_affixes import _batch, _receipt
from bench.profiles.text_native_compare import _load_native


if TYPE_CHECKING:
    from collections.abc import Callable


# These are fixture data, not assumptions about which files a checkout contains.
# Paths are committed public modules from b3c5c57; no discovery changes a run.
_PATHS = (
    "src/unstd/__init__.py",
    "src/unstd/audio/__init__.py",
    "src/unstd/audio/pcm.py",
    "src/unstd/audio/wavx.py",
    "src/unstd/clone/__init__.py",
    "src/unstd/clone/structural.py",
    "src/unstd/compress/__init__.py",
    "src/unstd/compress/bz2.py",
    "src/unstd/compress/engines/__init__.py",
    "src/unstd/compress/engines/flush.py",
    "src/unstd/compress/engines/imports.py",
    "src/unstd/compress/engines/native.py",
    "src/unstd/compress/engines/reader.py",
    "src/unstd/compress/gzipx.py",
    "src/unstd/compress/libarchive/__init__.py",
    "src/unstd/compress/lzma.py",
    "src/unstd/compress/tarfile.py",
    "src/unstd/compress/zipfile.py",
    "src/unstd/compress/zlib.py",
    "src/unstd/compress/zlibx.py",
    "src/unstd/compress/zstandard.py",
    "src/unstd/compress/zstd.py",
    "src/unstd/containers/__init__.py",
    "src/unstd/containers/ordering.py",
    "src/unstd/containers/persistent.py",
    "src/unstd/containers/sorted.py",
    "src/unstd/crypto/__init__.py",
    "src/unstd/crypto/digest.py",
    "src/unstd/crypto/hashing.py",
    "src/unstd/crypto/tls.py",
    "src/unstd/crypto/token.py",
    "src/unstd/extras.py",
    "src/unstd/fs/__init__.py",
    "src/unstd/fs/durability.py",
    "src/unstd/fs/traversal.py",
    "src/unstd/ids/__init__.py",
    "src/unstd/ids/uid.py",
    "src/unstd/iters/__init__.py",
    "src/unstd/iters/consecutive.py",
    "src/unstd/iters/distinct.py",
    "src/unstd/iters/reshape.py",
    "src/unstd/iters/select.py",
    "src/unstd/model/__init__.py",
    "src/unstd/model/base.py",
    "src/unstd/model/settings.py",
    "src/unstd/pack/__init__.py",
    "src/unstd/pack/binpack.py",
    "src/unstd/pack/buffers.py",
    "src/unstd/pack/dtypes.py",
    "src/unstd/pack/ragged.py",
    "src/unstd/proc/__init__.py",
    "src/unstd/proc/backend.py",
    "src/unstd/proc/results.py",
    "src/unstd/rand/__init__.py",
    "src/unstd/rand/crypto.py",
    "src/unstd/rand/draw.py",
    "src/unstd/rand/keyed.py",
    "src/unstd/rex/__init__.py",
    "src/unstd/rex/patterns.py",
    "src/unstd/rex/profile.py",
    "src/unstd/serde/__init__.py",
    "src/unstd/serde/b64.py",
    "src/unstd/serde/encoding.py",
    "src/unstd/serde/jsonx.py",
    "src/unstd/serde/ndjson.py",
    "src/unstd/serde/recursion.py",
    "src/unstd/serde/structs.py",
    "src/unstd/text/__init__.py",
    "src/unstd/text/fuzz.py",
    "src/unstd/time/__init__.py",
    "src/unstd/time/timeutil.py",
    "src/unstd/time/zoned.py",
    "src/unstd/toml/__init__.py",
    "src/unstd/toml/dialect.py",
    "src/unstd/yaml/__init__.py",
    "src/unstd/yaml/codec.py",
    "src/unstd/yaml/parser.py",
)
_FIRST_NAMES = [
    "Alice",
    "Benjamin",
    "Charlotte",
    "Daniel",
    "Eleanor",
    "Gabriel",
    "Hannah",
    "Isabella",
    "Jackson",
    "Katherine",
    "Liam",
    "Madeline",
    "Nathan",
    "Olivia",
    "Penelope",
    "Quentin",
    "Rebecca",
    "Samuel",
    "Theodore",
    "Victoria",
    "William",
    "Xavier",
    "Yvonne",
    "Zachary",
]
_LAST_NAMES = [
    "Anderson",
    "Bennett",
    "Carter",
    "Davidson",
    "Ellis",
    "Fitzgerald",
    "Garcia",
    "Harrison",
    "Ibrahim",
    "Johnson",
    "Kim",
    "Lewis",
    "Martinez",
    "Nguyen",
    "Ortiz",
    "Patel",
    "Quinn",
    "Robinson",
    "Smith",
    "Taylor",
    "Upton",
    "Vasquez",
    "Wilson",
    "Xu",
    "Young",
    "Zimmerman",
]


type _Operation = Literal["scalar", "best_match", "extract", "cdist"]


@dataclass(frozen=True)
class Case:
    """One complete public operation over an immutable fixture corpus."""

    name: str
    operation: _Operation
    queries: tuple[str, ...]
    choices: tuple[str, ...]
    cutoff: float = 0.75


def _cases() -> list[Case]:
    contacts = tuple(
        f"{first} {last}" for first in _FIRST_NAMES for last in _LAST_NAMES
    )
    cases: list[Case] = []
    operations: tuple[_Operation, ...] = ("scalar", "best_match", "extract", "cdist")
    for corpus, choices in (("contacts", contacts), ("paths", _PATHS)):
        queries = []
        for i in range(24):
            choice = choices[i * 37 % len(choices)]
            position = 3 if corpus == "contacts" else len(choice) // 2
            queries.append(choice[:position] + choice[position + 1 :])
        cases.extend(
            Case(f"{corpus}-{operation}", operation, tuple(queries), choices)
            for operation in operations
        )
    return cases


def _bundle(source: Path, binary: Path, label: str, *, extra: bool) -> Any:
    """Load source bytes directly, selecting imports only inside this wrapper."""
    native = _load_native(binary, label)
    path = source / "unstd/text/fuzz.py"
    spec = importlib.util.spec_from_file_location(f"_normal_jaro_{label}", path)
    if spec is None:
        msg = f"cannot load wrapper: {path}"
        raise RuntimeError(msg)
    wrapper = importlib.util.module_from_spec(spec)

    def selected_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if not extra and (name == "rapidfuzz" or name.startswith("rapidfuzz.")):
            msg = "this isolated wrapper selects the base install"
            raise ImportError(msg)
        if name == "unstd.text._text_native":
            return native
        return builtins.__import__(name, *args, **kwargs)

    wrapper.__dict__["__builtins__"] = {
        **vars(builtins),
        "__import__": selected_import,
    }
    sys.modules[spec.name] = wrapper
    # Compile recorded bytes rather than accepting a stale timestamp-based pyc.
    exec(compile(path.read_bytes(), str(path), "exec"), wrapper.__dict__)  # noqa: S102
    if wrapper._HAVE_RAPIDFUZZ is not extra:
        msg = f"incorrect optional-backend binding: {label}"
        raise AssertionError(msg)
    if not extra and wrapper.jaro_winkler is not wrapper._jaro_winkler:
        msg = f"incorrect public base scorer: {label}"
        raise AssertionError(msg)
    if wrapper._native_jaro is not native.jaro:
        msg = f"incorrect bundled native binding: {label}"
        raise AssertionError(msg)
    return wrapper


def _call(wrapper: Any, case: Case) -> Callable[[], Any]:
    scorer = wrapper.jaro_winkler
    if case.operation == "scalar":
        return partial(scorer, case.queries[12], case.choices[12])
    if case.operation == "cdist":
        return partial(
            wrapper.cdist,
            case.queries,
            case.choices,
            scorer=scorer,
            score_cutoff=case.cutoff,
        )
    options: dict[str, Any] = {"scorer": scorer, "score_cutoff": case.cutoff}
    if case.operation == "extract":
        options["limit"] = 5
    return partial(
        getattr(wrapper, case.operation), case.queries[12], case.choices, **options
    )


def _expected(case: Case, scalar: Callable[[str, str], float]) -> Any:
    """Exhaustive pairwise scoring, Python stable sorting, and inclusive cutoff."""
    if case.operation == "scalar":
        return scalar(case.queries[12], case.choices[12])
    if case.operation == "cdist":
        return [
            [s if (s := scalar(q, c)) >= case.cutoff else 0.0 for c in case.choices]
            for q in case.queries
        ]
    ranked = sorted(
        (
            (c, s)
            for c in case.choices
            if (s := scalar(case.queries[12], c)) >= case.cutoff
        ),
        key=lambda hit: hit[1],
        reverse=True,
    )
    return (
        (ranked[0] if ranked else None)
        if case.operation == "best_match"
        else ranked[:5]
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


def _check(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        msg = f"complete output differs from scalar oracle: {label}"
        raise AssertionError(msg)


def _contracts(wrapper: Any) -> None:
    """Check ties, duplicates, exact cutoffs, no hits, and zero-size results."""
    score = wrapper.jaro_winkler
    query, choices = "MARTHA", ("MARHTA", "MARHTA", "MATHRA", "ZZZZZZ")
    boundary = score(query, choices[0])
    cutoffs = (0.0, boundary, math.nextafter(boundary, math.inf), math.nan)
    for cutoff in cutoffs:
        ranked = sorted(
            ((c, s) for c in choices if (s := score(query, c)) >= cutoff),
            key=lambda hit: hit[1],
            reverse=True,
        )
        _check(
            wrapper.best_match(query, choices, scorer=score, score_cutoff=cutoff),
            ranked[0] if ranked else None,
            "boundary/best_match",
        )
        for limit in (0, 1, 2, 5, None):
            _check(
                wrapper.extract(
                    query, choices, scorer=score, score_cutoff=cutoff, limit=limit
                ),
                ranked[:limit],
                "boundary/extract",
            )
        _check(
            wrapper.cdist([query], choices, scorer=score, score_cutoff=cutoff),
            [[s if (s := score(query, c)) >= cutoff else 0.0 for c in choices]],
            "boundary/cdist",
        )
    _check(wrapper.best_match(query, (), scorer=score), None, "empty/best_match")
    _check(wrapper.extract(query, (), scorer=score), [], "empty/extract")
    _check(wrapper.cdist([], choices, scorer=score), [], "empty/cdist-queries")
    _check(wrapper.cdist([query], [], scorer=score), [[]], "empty/cdist-choices")


def _route(wrapper: Any, case: Case) -> dict[str, Any]:
    """Observe actual process calls through a local backend proxy."""
    backend = wrapper._rf
    events: list[str] = []

    if backend is None:
        native = wrapper._native_jaro
        native_events: list[bool] = []

        def recorded_native(a: str, b: str) -> Any:
            result = native(a, b)
            native_events.append(result is not NotImplemented)
            return result

        wrapper._native_jaro = recorded_native
        try:
            _call(wrapper, case)()
        finally:
            wrapper._native_jaro = native
        return {
            "scalar": "public base scorer",
            "batch": [],
            "native_calls": len(native_events),
            "native_accepted": sum(native_events),
            "native_declined": len(native_events) - sum(native_events),
            "public_scalar_module": wrapper.jaro_winkler.__module__,
        }

    class Observed:
        def __getattr__(self, name: str) -> Any:
            original = getattr(backend.process, name)

            def recorded(*args: Any, **kwargs: Any) -> Any:
                events.append(name)
                return original(*args, **kwargs)

            return recorded

    class Backend:
        process = Observed()

        def __getattr__(self, name: str) -> Any:
            return getattr(backend, name)

    wrapper._rf = Backend()
    try:
        _call(wrapper, case)()
    finally:
        wrapper._rf = backend
    return {
        "scalar": "RapidFuzz JaroWinkler.similarity",
        "batch": events,
        "public_scalar_is_backend": wrapper.jaro_winkler
        is backend.distance.JaroWinkler.similarity,
        "public_scalar_module": wrapper.jaro_winkler.__module__,
    }


def _summarize(samples: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    result: dict[str, Any] = {"samples": samples}
    for clock in ("wall", "cpu"):
        result[f"median_{clock}_ns"] = {
            arm: statistics.median(s[f"{clock}_ns"] for s in batch)
            for arm, batch in samples.items()
        }
    result["paired_speedup"] = statistics.median(
        before["wall_ns"] / after["wall_ns"]
        for before, after in zip(samples["baseline"], samples["candidate"], strict=True)
    )
    return result


def _backend_receipt(function: Any, *, native: bool) -> dict[str, Any]:
    """Identify the actual imported scorer/process implementation file."""
    path = sys.modules[function.__module__].__file__
    if path is None:
        msg = f"backend has no implementation file: {function.__module__}"
        raise RuntimeError(msg)
    if native and not any(path.endswith(suffix) for suffix in EXTENSION_SUFFIXES):
        msg = f"qualification requires compiled RapidFuzz: {function.__module__}"
        raise RuntimeError(msg)
    return {"module": function.__module__, "file": _receipt(Path(path))}


def main() -> None:
    """Save exact output checks and paired API timings before enforcing guards."""
    import numpy
    import rapidfuzz

    parser = argparse.ArgumentParser(description=__doc__)
    for arm in ("baseline", "candidate"):
        parser.add_argument(f"--{arm}-source", type=Path, required=True)
        parser.add_argument(f"--{arm}-native", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument(
        "--lanes", choices=("extra", "base"), nargs="+", default=["extra", "base"]
    )
    parser.add_argument("--max-slowdown", type=float, default=1.3)
    parser.add_argument("--minimum-speedup", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("at least three paired rounds are required")
    if not args.max_slowdown >= 1 or not args.minimum_speedup >= 0:
        parser.error("max-slowdown must be >= 1; minimum-speedup must be >= 0")
    arms = ("baseline", "candidate")
    sources = {arm: getattr(args, f"{arm}_source").resolve() for arm in arms}
    binaries = {arm: getattr(args, f"{arm}_native").resolve() for arm in arms}
    receipts = {
        arm: {
            "wrapper": _receipt(sources[arm] / "unstd/text/fuzz.py"),
            "native": _receipt(binaries[arm]),
        }
        for arm in arms
    }
    cases = _cases()
    report: dict[str, Any] = {
        "utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "rapidfuzz": rapidfuzz.__version__,
            "numpy": numpy.__version__,
            "gc_enabled": gc.isenabled(),
        },
        "implementations": receipts,
        "harness": [
            _receipt(Path(__file__)),
            _receipt(Path(__file__).with_name("text_affixes.py")),
            _receipt(Path(__file__).with_name("text_native_compare.py")),
            _receipt(Path(__file__).parents[1] / "core/timing.py"),
        ],
        "fixtures": {
            "sha256": _sha([vars(case) for case in cases]),
            "contacts": "24 fixed given names x 26 fixed surnames; 24 deletion-typo queries",
            "paths": "77 fixed committed module paths; 24 deletion-typo queries",
            "matching_cutoff": 0.75,
            "extract_limit": 5,
        },
        "method": "public APIs; complete outputs exactly equal exhaustive selected scalar scorer; checks before/after timings; independent >=5ms calibration; alternating paired wall/process-CPU batch means",
        "limitations": "synthetic contact names and frozen repository paths on the recorded host; excludes import startup, allocation profiling, I/O, and application-specific preprocessing; base and RapidFuzz scalar scores can differ by one float epsilon",
        "rows": {},
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    try:
        report["environment"]["rapidfuzz_backends"] = {
            name: _backend_receipt(function, native=native)
            for name, (function, native) in {
                "scalar": (rapidfuzz.distance.JaroWinkler.similarity, True),
                "process_wrapper": (rapidfuzz.process.cdist, False),
                "process_implementation": (
                    getattr(
                        sys.modules[rapidfuzz.process.cdist.__module__],
                        "_cdist",
                        rapidfuzz.process.cdist,
                    ),
                    True,
                ),
            }.items()
        }
        for lane in args.lanes:
            wrappers = {
                arm: _bundle(
                    sources[arm], binaries[arm], f"{lane}_{arm}", extra=lane == "extra"
                )
                for arm in arms
            }
            for wrapper in wrappers.values():
                _contracts(wrapper)
            for case in cases:
                calls = {arm: _call(wrapper, case) for arm, wrapper in wrappers.items()}
                expected = _expected(case, wrappers["baseline"].jaro_winkler)
                candidate_oracle = _expected(case, wrappers["candidate"].jaro_winkler)
                _check(candidate_oracle, expected, f"{lane}/{case.name}/scalar-oracle")
                for arm, call in calls.items():
                    _check(call(), expected, f"{lane}/{case.name}/{arm}/before")
                routes = {
                    arm: _route(wrapper, case) for arm, wrapper in wrappers.items()
                }
                if lane == "extra":
                    _check(
                        routes["candidate"]["public_scalar_is_backend"],
                        True,
                        f"{case.name}/public-scalar-binding",
                    )
                    if case.operation != "scalar":
                        method = {
                            "best_match": "extractOne",
                            "extract": "extract",
                            "cdist": "cdist",
                        }[case.operation]
                        _check(
                            routes["candidate"]["batch"],
                            [method],
                            f"{case.name}/required-RapidFuzz-process-route",
                        )
                else:
                    for arm in arms:
                        _check(
                            routes[arm]["native_accepted"] > 0,
                            True,
                            f"{case.name}/{arm}/required-bundled-native-route",
                        )
                        _check(
                            routes[arm]["native_declined"],
                            0,
                            f"{case.name}/{arm}/unexpected-native-fallback",
                        )
                counts = {arm: _calibrate(call)[0] for arm, call in calls.items()}
                samples: dict[str, list[dict[str, float]]] = {arm: [] for arm in arms}
                orders = []
                for i in range(args.rounds):
                    order = arms if i % 2 == 0 else arms[::-1]
                    orders.append(order)
                    for arm in order:
                        samples[arm].append(_batch(calls[arm], counts[arm]))
                observed = {}
                for arm, call in calls.items():
                    value = call()
                    _check(value, expected, f"{lane}/{case.name}/{arm}/after")
                    observed[arm] = _sha(value)
                row = {
                    "operation": case.operation,
                    "query_count": len(case.queries)
                    if case.operation == "cdist"
                    else 1,
                    "choice_count": len(case.choices),
                    "maximum_string_length": max(
                        map(len, (*case.queries, *case.choices))
                    ),
                    "exact_output_check": True,
                    "expected_sha256": _sha(expected),
                    "output_sha256": observed,
                    "routes": routes,
                    "iterations": counts,
                    "order": orders,
                    **_summarize(samples),
                }
                report["rows"][f"{lane}/{case.name}"] = row
                print(
                    f"{lane}/{case.name}: {row['median_wall_ns']['baseline'] / 1e3:.3f}us -> {row['median_wall_ns']['candidate'] / 1e3:.3f}us ({row['paired_speedup']:.2f}x)",
                    flush=True,
                )
                save()
        for arm in arms:
            _check(
                receipts[arm],
                {
                    "wrapper": _receipt(sources[arm] / "unstd/text/fuzz.py"),
                    "native": _receipt(binaries[arm]),
                },
                f"source/native receipt changed during timing: {arm}",
            )
    except Exception as error:
        report["invalidated"] = f"{type(error).__name__}: {error}"
        save()
        raise
    failures = []
    for name, row in report["rows"].items():
        if row["paired_speedup"] < 1 / args.max_slowdown:
            failures.append(f"{name}: slowdown exceeds {args.max_slowdown}x")
        if (
            name.startswith("extra/contacts-")
            and row["operation"] != "scalar"
            and row["paired_speedup"] < args.minimum_speedup
        ):
            failures.append(f"{name}: speedup below {args.minimum_speedup}x")
    report["guards"] = {
        "maximum_all_case_slowdown": args.max_slowdown,
        "minimum_extra_contacts_matching_speedup": args.minimum_speedup,
        "failures": failures,
    }
    save()
    if failures:
        raise AssertionError("; ".join(failures))


if __name__ == "__main__":
    main()
