"""Paired directory policy controls with independent inputs and balanced orders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
import hashlib
import itertools
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
from typing import TYPE_CHECKING, Protocol, cast, override

from reproduce import source_module


if TYPE_CHECKING:
    from types import ModuleType

type Pathish = str | os.PathLike[str]
type Operation = Callable[[], Path | None]
OUT = Path(__file__).resolve().parent
RELEASED = source_module(OUT / "before-fs.py.gz")


@dataclass(frozen=True)
class WrappedPath(os.PathLike[str]):
    """Custom path protocol input with one immutable lexical value."""

    value: str

    @override
    def __fspath__(self) -> str:
        return self.value


class EnsurePolicy(Protocol):
    """The optional directory probe policy shared by both candidates."""

    def __call__(self, path: Pathish, *, check_first: bool = True) -> Path: ...


def bind(api: ModuleType, path: Pathish, *, mode: bool | None = None) -> Operation:
    function: object = api.ensure_dir
    if not callable(function):
        msg = "Non-callable directory API"
        raise TypeError(msg)
    if mode is None:
        return partial(cast("Callable[[Pathish], Path]", function), path)
    return partial(cast("EnsurePolicy", function), path, check_first=mode)


def stdlib(path: Pathish) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def measure(
    guarded: ModuleType, normalized: ModuleType, *, shape: str, fresh: bool, mode: bool
) -> dict[str, object]:
    rounds, loops = 11, 48
    with tempfile.TemporaryDirectory(prefix="fs-guarded-directory-") as scratch:
        path = Path(scratch) / "directory"
        names = ("stdlib", "installed", "guarded", "normalized")
        values: dict[str, Pathish] = {}
        for name in names:
            independent = Path(scratch) / "directory"
            if shape == "cached_path":
                str(independent)
            if shape in {"path", "cached_path"}:
                values[name] = independent
            elif shape == "str":
                values[name] = str(independent)
            else:
                values[name] = WrappedPath(str(independent))
        baseline_value = values["stdlib"]
        baseline = partial(stdlib, baseline_value)
        if isinstance(baseline_value, Path):
            baseline = partial(baseline_value.mkdir, parents=True, exist_ok=True)
        operations: dict[str, Operation] = {
            "stdlib": baseline,
            "installed": bind(RELEASED, values["installed"]),
            "guarded": bind(guarded, values["guarded"], mode=mode),
            "normalized": bind(normalized, values["normalized"], mode=mode),
        }
        if not fresh:
            path.mkdir()
        orders = tuple(itertools.permutations(operations))
        samples: dict[str, dict[str, list[float]]] = {
            name: {"wall": [], "cpu": []} for name in operations
        }
        raw: dict[str, dict[str, list[float]]] = {
            name: {"wall": [], "cpu": []} for name in operations
        }
        for round_index in range(rounds):
            current: dict[str, dict[str, list[float]]] = {
                name: {"wall": [], "cpu": []} for name in operations
            }
            for index in range(loops):
                for name in orders[(index + round_index) % len(orders)]:
                    started_wall = time.perf_counter_ns()
                    started_cpu = time.process_time_ns()
                    operations[name]()
                    cpu = time.process_time_ns() - started_cpu
                    wall = time.perf_counter_ns() - started_wall
                    current[name]["wall"].append(wall)
                    current[name]["cpu"].append(cpu)
                    raw[name]["wall"].append(wall)
                    raw[name]["cpu"].append(cpu)
                    if fresh:
                        path.rmdir()
                        if path.exists():
                            msg = "Fresh directory survived cleanup"
                            raise RuntimeError(msg)
            for name in operations:
                for clock in ("wall", "cpu"):
                    samples[name][clock].append(statistics.median(current[name][clock]))
        medians = {
            name: {clock: statistics.median(values) for clock, values in clocks.items()}
            for name, clocks in samples.items()
        }
        pooled = {
            name: {clock: statistics.median(values) for clock, values in clocks.items()}
            for name, clocks in raw.items()
        }
        return {
            "method_revision": "shared-physical-path-independent-inputs-all-permutations-v3",
            "shape": shape,
            "state": "missing" if fresh else "existing",
            "check_first": mode,
            "rounds": rounds,
            "calls_per_round_per_arm": loops,
            "median_ns": medians,
            "pooled_median_ns": pooled,
            "round_medians_ns": samples,
            "raw_call_samples_ns": raw,
        }


def main() -> None:
    paths = [OUT / "guarded-cache-fs.py.gz", OUT / "normalization-control-fs.py.gz"]
    hashes = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    guarded, normalized = (source_module(path) for path in paths)
    rows: list[dict[str, object]] = []
    for shape in ("path", "cached_path", "str", "pathlike"):
        for fresh in (False, True):
            for mode in (True, False):
                row = measure(guarded, normalized, shape=shape, fresh=fresh, mode=mode)
                rows.append(row)
                print(
                    json.dumps(
                        {
                            k: row[k]
                            for k in ("shape", "state", "check_first", "median_ns")
                        }
                    ),
                    flush=True,
                )
    if not all(
        hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        for path, digest in hashes.items()
    ):
        message = "Proof source changed during measurement"
        raise RuntimeError(message)
    (OUT / "directory-reproduction.json").write_text(
        json.dumps({"sources_sha256": hashes, "workloads": rows}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
