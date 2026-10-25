"""Alternative clone algorithms, with strict public-contract checks.

This diagnostic compares public ``clone.deep``/``clone.asdict`` with fixed-shape
Python copiers and serialization roundtrips. A fast candidate whose strict
result check fails is not a valid replacement. Serialization costs include both
encoding and decoding; inputs and result checks are outside measurement.
"""

from __future__ import annotations

import argparse
from contextlib import suppress
import copy
import dataclasses
from functools import partial
import hashlib
import importlib
import json
import marshal
from pathlib import Path
import pickle
import platform
import sys
from typing import TYPE_CHECKING, Any

from bench.profiles import clone_common_cpu as common
from bench.profiles import clone_cpu as base


if TYPE_CHECKING:
    from collections.abc import Callable


def _roundtrip(
    encode: Callable[[Any], Any], decode: Callable[[Any], Any], source: Any
) -> Any:
    """Decode only the bytes just encoded from prepared local benchmark data."""
    return decode(encode(source))


def _marshal2(source: Any) -> bytes:
    """Encode the prepared source without marshal's shared-reference table."""
    return marshal.dumps(source, 2)


def _specialized(case: base.Workload) -> Callable[[], Any] | None:
    """Fixed-shape controls omit admission and arbitrary-protocol handling."""
    if case.name == "nested-200":
        return lambda: {
            "rows": [
                {"id": row["id"], "items": row["items"].copy()}
                for row in case.source["rows"]
            ]
        }
    if case.name == "records-500":
        return lambda: [row.copy() for row in case.source]
    if case.name == "dataclasses-200":

        def rows() -> list[base.Record]:
            output = []
            for row in case.source:
                result = object.__new__(base.Record)
                result.__dict__ = row.__dict__.copy()
                result.values = row.values.copy()
                output.append(result)
            return output

        return rows
    return None


def main() -> None:
    """Measure alternatives without treating failed contract checks as gains."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    from unstd.clone import structural as current

    modules = {"public": current}
    if args.compare_dir:
        modules["before"] = base._snapshot(
            args.compare_dir.resolve(), "algorithm_before"
        )
    optional = {}
    for name in ("orjson", "msgspec"):
        with suppress(ImportError):
            optional[name] = importlib.import_module(name)
    selected = {
        "nested-200",
        "records-500",
        "tuple-records-500",
        "dataclasses-200",
        "dataclass-records-500",
        "asdict-records-500",
    }
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "gc_enabled_during_batches": False,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_helper_sha256": hashlib.sha256(
            Path(base.__file__).read_bytes()
        ).hexdigest(),
        "workload_helper_sha256": hashlib.sha256(
            Path(common.__file__).read_bytes()
        ).hexdigest(),
        "optional_libraries": {
            name: getattr(module, "__version__", None)
            for name, module in optional.items()
        },
        "implementations": {
            label: base._metadata(module) for label, module in modules.items()
        },
        "workloads": {},
    }
    for case in common._workloads():
        if case.name not in selected:
            continue
        calls: dict[str, Callable[[], Any]] = {
            label: partial(getattr(module, case.operation), case.source)
            for label, module in modules.items()
        }
        calls["stdlib"] = partial(
            dataclasses.asdict if case.operation == "asdict" else copy.deepcopy,
            case.source,
        )
        calls["pickle5"] = partial(
            _roundtrip, partial(pickle.dumps, protocol=5), pickle.loads, case.source
        )
        calls["marshal2"] = partial(_roundtrip, _marshal2, marshal.loads, case.source)
        if "orjson" in optional:
            orjson = optional["orjson"]
            calls["orjson"] = partial(
                _roundtrip, orjson.dumps, orjson.loads, case.source
            )
        if "msgspec" in optional:
            msgspec = optional["msgspec"]
            calls["msgspec-json"] = partial(
                _roundtrip, msgspec.json.encode, msgspec.json.decode, case.source
            )
        specialized = _specialized(case)
        if specialized is not None:
            calls["specialized-python"] = specialized
        checks: dict[str, dict[str, Any]] = {}
        supported: dict[str, Callable[[], Any]] = {}
        for label, call in calls.items():
            try:
                actual = call()
                checks[label] = {
                    "strict_result_valid": common._check(
                        case, "stdlib" if label == "stdlib" else "current", actual
                    )
                }
            except (TypeError, ValueError, OverflowError) as error:
                checks[label] = {"error": f"{type(error).__name__}: {error}"}
            else:
                supported[label] = call
        output["workloads"][case.name] = {
            "checks": checks,
            "timings": base._timings(supported, args.rounds, False),
        }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
