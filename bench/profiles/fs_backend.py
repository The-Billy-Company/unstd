"""Paired filesystem backend measurements with real file and directory syncing.

Run this file directly to avoid importing the other benchmark groups. Saved
sources must contain ``unstd/fs``; both implementations use the same interpreter,
filesystem, and prepared trees. Checks and profiles run outside timing. Samples
measure warm-cache operations, including list construction for complete walks.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import dataclass
from functools import partial
import hashlib
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder, SourceFileLoader
import inspect
import json
from pathlib import Path
import platform
import pstats
import statistics
import sys
import tempfile
import time
from types import CodeType, ModuleType
from typing import TYPE_CHECKING, Any, override


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class _FreshSourceLoader(SourceFileLoader):
    """Compile the selected source bytes, ignoring potentially stale bytecode."""

    @override
    def get_code(self, fullname: str) -> CodeType:
        """Compile source directly without reading or writing a bytecode cache."""
        return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)


class _SourceFinder(MetaPathFinder):
    """Scope fresh source loading to the selected filesystem package."""

    @override
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Replace only the filesystem package's ordinary Python source loader."""
        if fullname != "unstd.fs" and not fullname.startswith("unstd.fs."):
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and isinstance(spec.loader, SourceFileLoader):
            assert spec.origin is not None
            spec.loader = _FreshSourceLoader(fullname, spec.origin)
        return spec


@dataclass
class Case:
    """Prepared operations and independent checks, with optional route setup."""

    name: str
    calls: dict[str, Callable[[], Any]]
    check: Callable[[str, Any], None]
    setup: Callable[[str], None]
    note: str
    batch_size: int = 1


def _load_source(source: Path) -> tuple[ModuleType, dict[str, Any]]:
    """Bind every fs import to its selected tree, then restore live imports."""
    source = source.resolve()
    package = source / "unstd" / "fs"
    if not (package / "__init__.py").is_file():
        message = f"source must contain unstd/fs/__init__.py: {source}"
        raise ValueError(message)
    saved = {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "unstd" or name.startswith("unstd.")
    }
    for name in saved:
        del sys.modules[name]
    parent = ModuleType("unstd")
    parent.__path__ = [str(source / "unstd")]
    sys.modules["unstd"] = parent
    finder = _SourceFinder()
    sys.meta_path.insert(0, finder)
    try:
        module = importlib.import_module("unstd.fs")
        imported = {
            name: item
            for name, item in sys.modules.copy().items()
            if name == "unstd.fs" or name.startswith("unstd.fs.")
        }
        files = {}
        for name, item in imported.items():
            filename = item.__file__
            assert filename is not None
            path = Path(filename).resolve()
            if not path.is_relative_to(package):
                message = f"filesystem internal escaped selected source: {name}: {path}"
                raise RuntimeError(message)
            files[str(path.relative_to(source))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        signatures = {
            name: str(inspect.signature(getattr(module, name)))
            for name in module.__all__
        }
        evidence = {
            "source": str(source),
            "files_sha256": files,
            "public_signatures": signatures,
            "descriptor_walk_supported": module._FD_WALK_SUPPORTED,
        }
        return module, evidence
    finally:
        sys.meta_path.remove(finder)
        for name in list(sys.modules):
            if name == "unstd" or name.startswith("unstd."):
                del sys.modules[name]
        sys.modules.update(saved)


def _check_signatures(evidence: dict[str, dict[str, Any]]) -> None:
    """Reject a source comparison whose public call contracts changed."""
    expected = next(iter(evidence.values()))["public_signatures"]
    for label, item in evidence.items():
        if item["public_signatures"] != expected:
            message = f"public fs signatures changed in {label}"
            raise RuntimeError(message)


def _check_source_hashes(evidence: dict[str, dict[str, Any]]) -> None:
    """Reject measurements if a selected source file changed while running."""
    for label, item in evidence.items():
        source = Path(item["source"])
        for relative, expected in item["files_sha256"].items():
            path = source / relative
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                message = f"fs source changed during measurement: {label}: {relative}"
                raise RuntimeError(message) from error
            if actual != expected:
                message = f"fs source changed during measurement: {label}: {relative}"
                raise RuntimeError(message)


def _walk(
    module: ModuleType, root: Path, *, follow_symlinks: bool = False
) -> list[Path]:
    return list(module.walk_files(root, follow_symlinks=follow_symlinks))


def _early_close(module: ModuleType, root: Path) -> Path | None:
    iterator = module.walk_files(root)
    try:
        return next(iterator, None)
    finally:
        iterator.close()


def _cases(
    modules: dict[str, ModuleType],
    root: Path,
    *,
    flat_files: int = 2000,
    directories: int = 64,
    files_per_directory: int = 32,
    depth: int = 80,
) -> list[Case]:
    """Prepare known trees once, without deriving expected files from a walk."""
    flat = root / "flat"
    nested = root / "nested"
    empty = root / "empty"
    deep = root / "deep"
    for directory in (flat, nested, empty, deep):
        directory.mkdir()
    expected: dict[Path, set[Path]] = {flat: set(), nested: set(), empty: set()}
    for index in range(flat_files):
        path = flat / f"file-{index}"
        path.write_bytes(b"x")
        expected[flat].add(path)
    for index in range(directories):
        directory = nested / f"dir-{index}"
        directory.mkdir()
        (empty / f"dir-{index}").mkdir()
        for item in range(files_per_directory):
            path = directory / f"file-{item}"
            path.write_bytes(b"x")
            expected[nested].add(path)
    leaf = deep
    for _ in range(depth):
        leaf = leaf / "d"
        leaf.mkdir()
    path = leaf / "visible"
    path.write_bytes(b"x")
    expected[deep] = {path}
    supported = {label: module._FD_WALK_SUPPORTED for label, module in modules.items()}

    def setup(label: str) -> None:
        modules[label].__dict__["_FD_WALK_SUPPORTED"] = supported[label]

    def portable(label: str) -> None:
        modules[label].__dict__["_FD_WALK_SUPPORTED"] = False

    def walk_case(name: str, tree: Path, *, force_portable: bool = False) -> Case:
        def check(_label: str, result: Any) -> None:
            assert type(result) is list
            assert all(isinstance(p, Path) for p in result)
            assert len(result) == len(expected[tree])
            assert set(result) == expected[tree]

        return Case(
            name,
            {label: partial(_walk, module, tree) for label, module in modules.items()},
            check,
            portable if force_portable else setup,
            f"{len(expected[tree])} files; warm directory cache; list(Path) result",
        )

    cases = [
        walk_case("walk-flat", flat),
        walk_case("walk-nested", nested),
        walk_case("walk-empty-directories", empty),
        walk_case("walk-deep", deep),
        walk_case("walk-portable", nested, force_portable=True),
    ]

    def check_early(_label: str, result: Any) -> None:
        assert isinstance(result, Path)
        assert result in expected[deep]

    cases.append(
        Case(
            "walk-early-close",
            {
                label: partial(_early_close, module, deep)
                for label, module in modules.items()
            },
            check_early,
            setup,
            f"first file at depth {depth}; close all active ancestors",
        )
    )
    links = root / "links"
    links.mkdir()
    try:
        (links / "alias").symlink_to(nested, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        linked = {links / "alias" / p.relative_to(nested) for p in expected[nested]}

        def check_follow(_label: str, result: Any) -> None:
            assert type(result) is list
            assert all(isinstance(p, Path) for p in result)
            assert len(result) == len(linked)
            assert set(result) == linked

        cases.append(
            Case(
                "walk-follow-symlinks",
                {
                    label: partial(_walk, module, links, follow_symlinks=True)
                    for label, module in modules.items()
                },
                check_follow,
                setup,
                "explicitly follows one directory symlink; no cycles",
            )
        )
    destination = root / "operations"
    destination.mkdir()
    destinations = dict.fromkeys(modules, destination)
    write_cases: list[tuple[str, str | bytes, dict[str, Any]]] = [
        ("atomic-write-bytes-1k", b"x" * 1024, {}),
        ("atomic-write-text-1k", "café\n" * 200, {}),
        ("atomic-write-bytes-1m", b"x" * 1024**2, {}),
        ("atomic-write-durable", b"x" * 1024, {"durable": True}),
    ]
    try:
        for module in modules.values():
            module.sync_dir(root, strict=True)
    except OSError:
        pass
    else:
        write_cases.append(("atomic-write-strict", b"x" * 1024, {"durable": "strict"}))
    for name, payload, options in write_cases:
        calls: dict[str, Callable[[], Any]] = {}
        wanted = payload.encode() if isinstance(payload, str) else payload
        for label, module in modules.items():
            dest = destinations[label] / name
            dest.write_bytes(b"previous")
            calls[label] = partial(module.atomic_write, dest, payload, **options)

        def check_write(
            label: str, result: Any, data: bytes = wanted, filename: str = name
        ) -> None:
            assert isinstance(result, Path)
            assert result == destinations[label] / filename
            assert result.read_bytes() == data
            assert not list(result.parent.glob(f".{result.name}.*.tmp"))

        cases.append(
            Case(
                name,
                calls,
                check_write,
                setup,
                "rewrite existing file; real fsync; creation excluded",
            )
        )

    def writer(module: ModuleType, dest: Path) -> Path:
        with module.atomic_writer(dest, mode="wb") as stream:
            stream.write(b"complete payload")
        return dest

    def check_writer(label: str, result: Any) -> None:
        assert isinstance(result, Path)
        assert result == destinations[label] / "writer"
        assert result.read_bytes() == b"complete payload"
        assert not list(result.parent.glob(".writer.*.tmp"))

    cases.append(
        Case(
            "atomic-writer",
            {
                label: partial(writer, module, destinations[label] / "writer")
                for label, module in modules.items()
            },
            check_writer,
            setup,
            "binary context manager; real fsync; existing destination after warmup",
        )
    )

    def check_directory(label: str, result: Any) -> None:
        assert isinstance(result, Path)
        assert result == destinations[label]
        assert result.is_dir()

    cases.append(
        Case(
            "ensure-dir-existing",
            {
                label: partial(module.ensure_dir, destinations[label])
                for label, module in modules.items()
            },
            check_directory,
            setup,
            "already existing directory; no tree mutation",
            batch_size=1000,
        )
    )

    def create_directory(module: ModuleType, path: Path) -> Path:
        result: Path = module.ensure_dir(path)
        path.rmdir()
        return result

    def check_created(label: str, result: Any) -> None:
        assert isinstance(result, Path)
        assert result == destinations[label] / "created"
        assert not result.exists()

    cases.append(
        Case(
            "ensure-dir-create-remove",
            {
                label: partial(
                    create_directory, module, destinations[label] / "created"
                )
                for label, module in modules.items()
            },
            check_created,
            setup,
            "one ensure_dir plus rmdir to restore fixture; combined creation/removal cost",
            batch_size=100,
        )
    )

    def replace(module: ModuleType, first: Path, second: Path) -> tuple[Path, Path]:
        result = module.atomic_replace(first, second)
        restored = module.atomic_replace(second, first)
        return result, restored

    def check_replace(label: str, result: Any) -> None:
        first, second = destinations[label] / "first", destinations[label] / "second"
        assert result == (second, first)
        assert all(isinstance(p, Path) for p in result)
        assert first.read_bytes() == b"move payload"
        assert not second.exists()

    for dest in destinations.values():
        (dest / "first").write_bytes(b"move payload")
    cases.append(
        Case(
            "atomic-replace-pair",
            {
                label: partial(
                    replace,
                    module,
                    destinations[label] / "first",
                    destinations[label] / "second",
                )
                for label, module in modules.items()
            },
            check_replace,
            setup,
            "two atomic_replace calls restore the original fixture; no content fsync",
            batch_size=100,
        )
    )

    def check_sync(_label: str, result: Any) -> None:
        assert result is None

    cases.append(
        Case(
            "sync-dir",
            {
                label: partial(module.sync_dir, destinations[label])
                for label, module in modules.items()
            },
            check_sync,
            setup,
            "real best-effort directory fsync; suppressed OS errors retain API semantics",
            batch_size=100,
        )
    )
    return cases


def _batch(call: Callable[[], Any], repetitions: int) -> tuple[dict[str, int], Any]:
    cpu_start = time.process_time_ns()
    wall_start = time.perf_counter_ns()
    result = None
    for _ in range(repetitions):
        result = call()
    wall_ns = time.perf_counter_ns() - wall_start
    cpu_ns = time.process_time_ns() - cpu_start
    return {
        "wall_ns": wall_ns,
        "process_cpu_ns": cpu_ns,
        "operations": repetitions,
    }, result


def _profile(call: Callable[[], Any]) -> list[dict[str, Any]]:
    profiler = cProfile.Profile()
    profiler.runcall(call)
    profile_stats: Any = pstats.Stats(profiler)
    stats = profile_stats.stats
    rows = [
        {
            "file": file,
            "line": line,
            "function": function,
            "calls": counts[1],
            "self_seconds": counts[2],
            "cumulative_seconds": counts[3],
        }
        for (file, line, function), counts in stats.items()
    ]
    return sorted(rows, key=lambda item: item["cumulative_seconds"], reverse=True)[:15]


def _measure(
    case: Case, *, rounds: int, repetitions: int, minimum_ns: int = 20_000_000
) -> dict[str, Any]:
    """Check both sides first; alternate paired order and retain every sample."""
    labels = list(case.calls)
    repetitions *= case.batch_size
    for label in labels:
        case.setup(label)
        case.check(label, case.calls[label]())
    calibration = {}
    calibrated = repetitions
    if minimum_ns:
        for label in labels:
            case.setup(label)
            sample, result = _batch(case.calls[label], repetitions)
            case.check(label, result)
            calibration[label] = sample
            needed = (minimum_ns * repetitions + sample["wall_ns"] - 1) // max(
                1, sample["wall_ns"]
            )
            calibrated = max(calibrated, min(10_000, needed))
    repetitions = calibrated
    samples: dict[str, list[dict[str, int]]] = {label: [] for label in labels}
    orders = []
    for index in range(rounds):
        order = labels if index % 2 == 0 else labels[::-1]
        orders.append(list(order))
        for label in order:
            case.setup(label)
            sample, result = _batch(case.calls[label], repetitions)
            case.check(label, result)
            samples[label].append(sample)
    medians = {
        label: {
            metric: statistics.median(
                sample[metric] / sample["operations"] for sample in values
            )
            for metric in ("wall_ns", "process_cpu_ns")
        }
        for label, values in samples.items()
    }
    profiles = {}
    for label in labels:
        case.setup(label)
        profiles[label] = _profile(case.calls[label])
    ratios = {}
    if "before" in medians:
        ratios = {
            metric: medians["before"][metric] / medians["current"][metric]
            for metric in ("wall_ns", "process_cpu_ns")
        }
    return {
        "name": case.name,
        "note": case.note,
        "calibration": calibration,
        "target_batch_wall_ns": minimum_ns,
        "samples": samples,
        "median_per_operation_ns": medians,
        "before_over_current": ratios,
        "orders": orders,
        "profiles": profiles,
    }


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        message = "must be a positive integer"
        raise argparse.ArgumentTypeError(message)
    return number


def main(argv: list[str] | None = None) -> None:
    """Prepare fixtures, verify sources, and write raw paired evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=_positive, default=7)
    parser.add_argument("--repetitions", type=_positive, default=3)
    parser.add_argument(
        "--include", action="append", help="select an exact workload name; repeatable"
    )
    args = parser.parse_args(argv)
    sources = {"current": args.source}
    if args.compare_source is not None:
        sources["before"] = args.compare_source
    modules, evidence = {}, {}
    for label, source in sources.items():
        modules[label], evidence[label] = _load_source(source)
    _check_signatures(evidence)
    report = {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "filesystem_device": args.output.parent.resolve().stat().st_dev
            if args.output.parent.exists()
            else None,
        },
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": evidence,
        "rounds": args.rounds,
        "repetitions": args.repetitions,
        "method": "paired alternating warm-cache wall/process CPU batches; real fsync; checks and profiles excluded from timing; mutation fixtures shared between sources; GC left in its existing state",
        "workloads": [],
    }
    with tempfile.TemporaryDirectory(prefix="unstd-fs-profile-") as temporary:
        root = Path(temporary)
        report["environment"]["fixture_device"] = root.stat().st_dev
        cases = _cases(modules, root)
        if args.include:
            unknown = set(args.include) - {case.name for case in cases}
            if unknown:
                parser.error(f"unknown or unsupported workloads: {sorted(unknown)}")
            cases = [case for case in cases if case.name in args.include]
        for case in cases:
            result = _measure(case, rounds=args.rounds, repetitions=args.repetitions)
            report["workloads"].append(result)
            ratio = result["before_over_current"].get("wall_ns")
            print(f"{case.name}: {ratio:.3f}x" if ratio else case.name, flush=True)
    _check_source_hashes(evidence)
    if (
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        != report["harness_sha256"]
    ):
        message = "benchmark harness changed during measurement"
        raise RuntimeError(message)
    report["source_hashes_verified_after_timing"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
