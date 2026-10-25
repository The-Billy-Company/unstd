"""A favorable empty-node allocation control beside public clone operations.

Build ``clone_alloc_floor.c`` as ``_clone_alloc_floor`` with the selected Python
interpreter, then pass its extension path using ``--module-path``. The control
allocates empty list/dict/record nodes only, excluding input traversal, payload
references and hash tables. It is a diagnostic comparison, not a formal lower
bound or a production copy algorithm. All actual clone timings call the public
Python wrappers and include output destruction. GC is disabled during batches.
"""

from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
from pathlib import Path
import platform
import sys
from typing import Any

from bench.profiles import clone_common_cpu as common
from bench.profiles import clone_cpu as base


def main() -> None:
    """Measure public cloning beside prepared counts of empty output nodes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-path", type=Path, required=True)
    parser.add_argument("--compare-dir", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    from unstd.clone import structural as current

    extension = args.module_path.resolve()
    control = base._load(extension, "_clone_alloc_floor")
    modules = {"current": current}
    if args.compare_dir:
        modules["before"] = base._snapshot(args.compare_dir.resolve(), "alloc_before")
    source = Path(__file__).with_name("clone_alloc_floor.c")
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "gc_enabled_during_batches": False,
        "diagnostic": "favorable empty-node allocation control; no formal lower bound",
        "control_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "control_extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_helper_sha256": hashlib.sha256(
            Path(base.__file__).read_bytes()
        ).hexdigest(),
        "workload_helper_sha256": hashlib.sha256(
            Path(common.__file__).read_bytes()
        ).hexdigest(),
        "implementations": {
            label: base._metadata(module) for label, module in modules.items()
        },
        "workloads": {},
    }
    selected = {
        "nested-200",
        "records-500",
        "dataclasses-200",
        "dataclass-records-500",
        "asdict-records-500",
    }
    for case in common._workloads():
        if case.name not in selected:
            continue
        counts = common._node_counts(case.source, plain=case.operation == "asdict")
        calls = base._callables(case, modules)
        for label, call in calls.items():
            if not common._check(case, label, call()):
                message = f"incorrect public output: {case.name}/{label}"
                raise AssertionError(message)
        calls["empty_node_control"] = partial(
            control.nodes,
            counts["lists"],
            counts["dicts"],
            counts["records"],
            base.Record,
        )
        nodes = calls["empty_node_control"]()
        if len(nodes) != counts["lists"] + counts["dicts"] + counts["records"]:
            message = f"wrong empty-node count: {case.name}"
            raise AssertionError(message)
        del nodes
        output["workloads"][case.name] = {
            "node_counts": counts,
            "timings": base._timings(calls, args.rounds, False),
        }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
