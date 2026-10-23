"""Read-only, bounded candidate audit; no package or dependency edits."""

import argparse
from collections.abc import Callable, Sequence
from functools import partial
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
from typing import Any, cast
import uuid

import numpy as np
import uuid_utils as native
import uuid_utils.compat as compat

from unstd import rand
from unstd.ids import uid
from unstd.rand import crypto, draw


def require(condition: bool, description: str) -> None:
    """Raise when a pre-timing contract check fails."""
    if not condition:
        raise ValueError(description)


def _make_engine(cls: Callable[[int], np.random.BitGenerator]) -> np.random.Generator:
    """Construct a seeded engine without default-argument lambda typing gaps."""
    return np.random.Generator(cls(817))


def main(output: Path, *, validate_only: bool = False) -> int:
    """Measure fixed candidate workloads and save all raw evidence."""
    rows: dict[str, dict[str, Any]] = {}
    functions: dict[str, Callable[[], object]] = {}

    def add(name: str, fn: Callable[[], object]) -> None:
        """Register one fixed workload."""
        functions[name] = fn

    pool = list(range(64))
    weights = cast("Sequence[float]", np.arange(1.0, 65.0))
    engines = {
        name: np.random.Generator(getattr(np.random, name)(817))
        for name in ["PCG64", "PCG64DXSM", "SFC64", "Philox"]
    }
    py = random.Random(817)
    for name, gen in engines.items():
        for n in [1, 16, 1024, 100000]:
            add(
                f"bulk/{name}/floats/{n}",
                partial(draw._floats, py, gen, n, 0.0, 1.0),
            )
            add(
                f"bulk/{name}/ints/{n}",
                partial(draw._ints, py, gen, n, -100, 100, False),
            )
            add(
                f"bulk/{name}/choices/{n}",
                partial(draw._choices, py, gen, pool, n, None),
            )
            add(
                f"bulk/{name}/weighted/{n}",
                partial(draw._choices, py, gen, pool, n, weights),
            )
        cls = getattr(np.random, name)
        add(f"init/{name}", partial(_make_engine, cls))
    for n in [1, 16, 1024]:
        add(f"bulk/stdlib/floats/{n}", partial(draw._floats, py, None, n, 0.0, 1.0))
        add(
            f"bulk/stdlib/ints/{n}",
            partial(draw._ints, py, None, n, -100, 100, False),
        )
        add(
            f"bulk/stdlib/choices/{n}",
            partial(draw._choices, py, None, pool, n, None),
        )
        add(
            f"bulk/stdlib/weighted/{n}",
            partial(draw._choices, py, None, pool, n, weights),
        )
    add("scalar/stdlib/random", rand.random)
    add("scalar/numpy/random", engines["PCG64"].random)
    add("scalar/stdlib/randint", lambda: rand.randint(-100, 100))
    add("scalar/numpy/integers", lambda: engines["PCG64"].integers(-100, 101))
    add("scalar/stdlib/uniform", lambda: rand.uniform(-2.0, 3.0))
    add("scalar/numpy/uniform", lambda: engines["PCG64"].uniform(-2.0, 3.0))
    add("scalar/stdlib/choice", lambda: rand.choice(pool))
    add("scalar/numpy/choice", lambda: engines["PCG64"].choice(pool))
    add("scalar/stdlib/shuffle64", lambda: rand.shuffle(pool))
    add("scalar/stdlib/sample8of64", lambda: rand.sample(pool, 8))
    add("init/stdlib", lambda: random.Random(817))
    add("init/public-stdlib", lambda: rand.Generator(817))
    add("init/public-numpy", lambda: rand.Generator(817, backend="numpy"))
    add("seed/public-module", lambda: rand.seed(817))
    add("crypto/token_bytes32", lambda: crypto.token_bytes(32))
    add("crypto/token_hex32", lambda: crypto.token_hex(32))
    add("crypto/token_urlsafe32", lambda: crypto.token_urlsafe(32))
    add("crypto/below97", lambda: crypto.below(97))
    add("crypto/bits64", lambda: crypto.bits(64))
    add("crypto/choice64", lambda: crypto.choice(pool))
    add("crypto/os_urandom32", lambda: os.urandom(32))
    add("ids/new", uid.new)
    add("ids/new_hex", uid.new_hex)
    add("ids/native_object", native.uuid7)
    add("ids/compat_object", compat.uuid7)
    add("ids/fallback_object", uid._stdlib_uuid7)
    add("ids/fallback_new", lambda: str(uid._stdlib_uuid7()))
    add("ids/fallback_hex", lambda: uid._stdlib_uuid7().hex)
    ns = uuid.NAMESPACE_DNS
    nns = native.NAMESPACE_DNS
    add("ids/derive_stdlib", lambda: uid.derive(ns, "python.org"))
    add("ids/derive_native", lambda: native.uuid5(nns, "python.org"))
    add("ids/derive_compat", lambda: compat.uuid5(ns, "python.org"))
    add("ids/parse_stdlib", lambda: uid.parse("886313e1-3b8a-5372-9b90-0c9aee199e5d"))
    add("ids/parse_native", lambda: native.UUID("886313e1-3b8a-5372-9b90-0c9aee199e5d"))

    # Same output properties; stream identity explicitly changes with engine.
    for gen in engines.values():
        require(
            getattr(draw._floats(py, gen, 16, 0.0, 1.0), "shape", None) == (16,),
            "float shape",
        )
        values = draw._ints(py, gen, 16, -100, 100, False)
        require(getattr(values, "dtype", None) == np.int64, "integer dtype")
        require(
            bool(np.all((np.asarray(values) >= -100) & (np.asarray(values) < 100))),
            "integer bounds",
        )
        require(
            set(draw._choices(py, gen, pool, 16, None)) <= set(pool),
            "choice membership",
        )
        require(len(draw._choices(py, gen, pool, 16, weights)) == 16, "weighted size")
    require(
        str(native.uuid5(nns, "python.org"))
        == str(compat.uuid5(ns, "python.org"))
        == str(uid.derive(ns, "python.org"))
        == "886313e1-3b8a-5372-9b90-0c9aee199e5d",
        "UUIDv5 vector",
    )

    if validate_only:
        print(json.dumps({"validated_workloads": len(functions)}))
        return 0

    # Calibrate once to >=3ms, then five interleaved, rotating-order rounds.
    loops = {}

    def batch(fn: Callable[[], object], n: int) -> float:
        """Measure one complete invocation loop in wall-clock nanoseconds."""
        start = time.perf_counter_ns()
        for _ in range(n):
            fn()
        return (time.perf_counter_ns() - start) / n

    oldgc = gc.isenabled()
    gc.disable()
    try:
        for name, fn in functions.items():
            n = 1
            while True:
                cost = batch(fn, n)
                if cost * n >= 3000000 or n >= 1048576:
                    break
                n *= 2
            loops[name] = n
            rows[name] = {"loops": n, "samples_ns": []}
        keys = list(functions)
        for round_ in range(5):
            keys = keys[len(keys) // 5 :] + keys[: len(keys) // 5]
            if round_ % 2:
                keys.reverse()
            for name in keys:
                rows[name]["samples_ns"].append(batch(functions[name], loops[name]))
    finally:
        if oldgc:
            gc.enable()
    for row in rows.values():
        row["median_ns"] = statistics.median(row["samples_ns"])
        row["min_ns"] = min(row["samples_ns"])
        row["max_ns"] = max(row["samples_ns"])
        row["spread_fraction"] = (row["max_ns"] - row["min_ns"]) / row["median_ns"]

    imports = {}
    for module in ["", "numpy", "unstd.rand", "uuid_utils", "unstd.ids.uid"]:
        samples = []
        code = (
            "import resource,time,json; start=time.perf_counter_ns(); "
            + (f"import {module}; " if module else "")
            + "print(json.dumps({'import_ns':time.perf_counter_ns()-start,'peak_rss':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))"
        )
        for _ in range(3):
            completed = subprocess.run(  # noqa: S603 -- fixed interpreter and fixed import-only snippets
                [sys.executable, "-c", code], check=True, capture_output=True, text=True
            )
            samples.append(json.loads(completed.stdout))
        imports[module or "empty_process"] = samples
    state = {}
    for name, gen in engines.items():

        def normalize(obj: object) -> object:
            """Convert engine state into JSON-compatible values."""
            if isinstance(obj, np.ndarray):
                return cast("object", obj.tolist())
            if isinstance(obj, dict):
                return {k: normalize(v) for k, v in obj.items()}
            return obj

        state[name] = {
            "generator_size": sys.getsizeof(gen),
            "bitgenerator_size": sys.getsizeof(gen.bit_generator),
            "state": normalize(gen.bit_generator.state),
        }
    result = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "uuid_utils": importlib.metadata.version("uuid-utils"),
            "pid": os.getpid(),
            "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rss_units": "bytes on macOS, KiB on Linux",
        },
        "method": {
            "rounds": 5,
            "calibration_ns": 3000000,
            "rotating_interleaved_order": True,
            "gc_disabled": True,
            "timings_include_wrappers": True,
            "limitation": "Single machine exploratory evidence; no hard no-overhead or end-to-end guarantee.",
        },
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": {
            str(Path(cast("str", m.__file__)).resolve()): hashlib.sha256(
                Path(cast("str", m.__file__)).read_bytes()
            ).hexdigest()
            for m in [draw, crypto, uid]
        },
        "measurements": rows,
        "cold_imports": imports,
        "engine_memory": state,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"workloads": len(rows), "output": str(output.resolve())}))
    for op in ["floats", "ints", "choices", "weighted"]:
        for n in [1, 16, 1024, 100000]:
            baseline = rows[f"bulk/PCG64/{op}/{n}"]["median_ns"]
            print(
                op,
                n,
                {
                    name: round(
                        baseline / rows[f"bulk/{name}/{op}/{n}"]["median_ns"], 3
                    )
                    for name in engines
                },
            )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(args.output, validate_only=args.validate_only))
