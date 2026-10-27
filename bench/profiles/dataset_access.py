"""Qualify indexed dataset access against SQLite and the portable mapped reader.

Run ``python -m bench.profiles.dataset_access --output bench/reports/dataset/access.json``.
The SQLite reference uses the same JSON encoder/decoders and immutable snapshot
semantics. Seven matched alternating rounds retain every wall sample. Native
binding counters and correctness checks run outside timing; source drift refuses
publication. No external checkout or private application data is required.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import random
import shutil
import sqlite3
from statistics import median
import sys
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any, Protocol, cast

from unstd import fs
from unstd.dataset import Dataset, build
import unstd.dataset.storage as storage
from unstd.serde import jsonx, ndjson


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable, Iterable, Iterator

    from unstd.dataset._dataset_native import Mapping
    from unstd.serde.jsonx import Json


_ROOT = Path(__file__).resolve().parents[2]
_READS = ("open-count", "index-1024", "take-64", "take-4096", "scan", "batches-1024")


class _Rows(Protocol):
    """The common logical read operations used by all measured implementations."""

    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> Json: ...
    def __iter__(self) -> Iterator[Json]: ...
    def take(self, indices: Iterable[int]) -> list[Json]: ...
    def batches(self, size: int) -> Iterator[list[Json]]: ...


class _Native(Protocol):
    """The checked offset-copy functions provided by the optional C module."""

    __file__: str | None
    KERNEL_VERSION: int

    def map(self, descriptor: int, /) -> Mapping | None: ...

    def row(
        self, data: Buffer, index_offset: int, count: int, position: int, /
    ) -> bytes: ...
    def take(
        self, data: Buffer, index_offset: int, count: int, positions: tuple[int, ...], /
    ) -> list[bytes]: ...
    def span(
        self, data: Buffer, index_offset: int, count: int, start: int, stop: int, /
    ) -> bytes: ...
    def validate(self, data: Buffer, index_offset: int, count: int, /) -> None: ...


def _row(index: int, payload_bytes: int) -> Json:
    """A deterministic public fixture with scalar, text, and nested JSON fields."""
    text = sha256(f"dataset-record:{index}".encode()).hexdigest()
    return {
        "id": index,
        "group": index % 17,
        "text": (text * ((payload_bytes + 63) // 64))[:payload_bytes],
        "values": [index % 31, None, bool(index % 3)],
    }


def _encode(value: Json) -> bytes:
    """The identical finite JSON encoding used by both snapshot builders."""
    return jsonx.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


def _sqlite_build(path: Path, source: Path) -> int:
    """The former SQLite storage approach, including finalized-file copying."""
    count = 0

    def encoded() -> Iterator[tuple[int, bytes]]:
        nonlocal count
        with source.open("rb") as stream:
            for value in ndjson.iter_loads(stream):
                yield count, _encode(value)
                count += 1

    with TemporaryDirectory(
        prefix="dataset-sqlite-reference-", dir=path.parent
    ) as work:
        scratch = Path(work) / "snapshot.sqlite3"
        with closing(sqlite3.connect(scratch)) as connection:
            connection.execute("PRAGMA cache_size = -2048")
            connection.execute(
                "CREATE TABLE records (position INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK (id = 1), row_count INTEGER NOT NULL CHECK (row_count >= 0))"
            )
            with connection:
                connection.executemany("INSERT INTO records VALUES (?, ?)", encoded())
                connection.execute("INSERT INTO metadata VALUES (1, ?)", (count,))
        with scratch.open("rb") as reader, fs.atomic_writer(path, mode="wb") as writer:
            shutil.copyfileobj(reader, writer, length=1 << 20)
    return count


def _mapped_build(path: Path, source: Path) -> int:
    """The public streaming builder, including JSONL ingestion and publication."""
    with source.open("rb") as stream:
        return build(path, ndjson.iter_loads(stream))


class _SqliteRows:
    """Read-only indexed SQLite reference with metadata and endpoint admission."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(
            path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        self.connection.execute("PRAGMA cache_size = -2048")
        self.count: int = self.connection.execute(
            "SELECT row_count FROM metadata WHERE id = 1"
        ).fetchone()[0]
        first = self.connection.execute(
            "SELECT position FROM records ORDER BY position LIMIT 1"
        ).fetchone()
        last = self.connection.execute(
            "SELECT position FROM records ORDER BY position DESC LIMIT 1"
        ).fetchone()
        if first != (0,) or last != (self.count - 1,):
            message = "invalid SQLite reference endpoints"
            raise AssertionError(message)

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index: int) -> Json:
        if not 0 <= index < self.count:
            raise IndexError(index)
        with closing(
            self.connection.execute(
                "SELECT payload FROM records WHERE position = ?", (index,)
            )
        ) as cursor:
            return cast("Json", jsonx.loads(cursor.fetchone()[0]))

    def take(self, indices: Iterable[int]) -> list[Json]:
        return [self[index] for index in indices]

    def __iter__(self) -> Iterator[Json]:
        with closing(
            self.connection.execute(
                "SELECT position, payload FROM records ORDER BY position"
            )
        ) as cursor:
            count = 0
            while record := cursor.fetchone():
                if record[0] != count:
                    message = "SQLite reference contains a missing row"
                    raise AssertionError(message)
                yield jsonx.loads(record[1])
                count += 1
            if count != self.count:
                message = "SQLite reference count differs from its metadata"
                raise AssertionError(message)

    def batches(self, size: int) -> Iterator[list[Json]]:
        with closing(
            self.connection.execute("SELECT payload FROM records ORDER BY position")
        ) as cursor:
            while records := cursor.fetchmany(size):
                # The stronger SQLite control uses the same bulk decoder, not
                # one Python decoder call for every row in a batch.
                yield ndjson.loads(b"\n".join(record[0] for record in records))

    def close(self) -> None:
        self.connection.close()


def _aggregate(values: Iterable[Json]) -> Json:
    """A full-scan result independently checked by arithmetic over row indices."""
    count = total = groups = characters = 0
    for value in values:
        row = cast("dict[str, Json]", value)
        count += 1
        total += cast("int", row["id"])
        groups += cast("int", row["group"])
        characters += len(cast("str", row["text"]))
    return {
        "rows": count,
        "id_sum": total,
        "group_sum": groups,
        "text_chars": characters,
    }


def _origin(path: Path) -> str:
    """A reproducible source label without machine-specific checkout prefixes."""
    try:
        relative = path.resolve().relative_to(_ROOT)
    except ValueError:
        return path.name
    parts = relative.parts
    if "site-packages" in parts:
        return "/".join(parts[parts.index("site-packages") + 1 :])
    if "unstd" in parts:
        return "/".join(parts[parts.index("unstd") :])
    return relative.as_posix()


def _provenance(native: _Native) -> dict[str, Any]:
    """Content-bound product, decoder, kernel, and benchmark identities."""
    files = {
        "proof": Path(__file__),
        "native_source": _ROOT / "native/dataset/src/adapter.c",
    }
    for name in (
        "unstd.dataset",
        "unstd.dataset.reader",
        "unstd.dataset.storage",
        "unstd.serde.jsonx",
        "unstd.serde.ndjson",
        "unstd.serde.encoding",
        "unstd.serde.recursion",
        "unstd.fs",
        "unstd.serde._json_native",
        "msgspec._core",
        "orjson.orjson",
    ):
        module = sys.modules.get(name)
        if module is not None and (filename := getattr(module, "__file__", None)):
            files[name] = Path(filename)
    if native.__file__ is None:
        message = "native dataset module has no file provenance"
        raise RuntimeError(message)
    files["dataset_native"] = Path(native.__file__)
    versions: dict[str, str | None] = {}
    for name in ("orjson", "msgspec"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "files": {
            name: {
                "path": _origin(path),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in files.items()
        },
        "backends": versions,
    }


@contextmanager
def _backend(native: _Native, enabled: bool) -> Iterator[None]:
    """Select the actual package dispatch outside any timed operation."""
    original = storage._native
    storage._native = native if enabled else None
    try:
        yield
    finally:
        storage._native = original


@contextmanager
def _observe(native: _Native) -> Iterator[Counter[str]]:
    """Count real bound C entrypoints in a separate untimed verification pass."""
    counts: Counter[str] = Counter()
    originals = {
        name: getattr(native, name)
        for name in ("map", "row", "take", "span", "validate")
    }
    for name, original in originals.items():

        def counted(*args: Any, _name: str = name, _original: Any = original) -> Any:
            counts[_name] += 1
            return _original(*args)

        setattr(native, name, counted)
    try:
        yield counts
    finally:
        for name, original in originals.items():
            setattr(native, name, original)


def _open_mapped(path: Path, native: _Native, enabled: bool) -> Dataset:
    """Construct each control with its own actual mapping implementation."""
    with _backend(native, enabled):
        return Dataset(path)


def _checked(run: Callable[[], Json], expected: Json) -> Json:
    """Independent output comparison outside the timer."""
    actual = run()
    if actual != expected:
        message = (
            "dataset qualification output differs from independently expected records"
        )
        raise AssertionError(message)
    return actual


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=25_000)
    parser.add_argument("--payload-bytes", type=int, default=256)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument(
        "--output", type=Path, default=Path("bench/reports/dataset/access.json")
    )
    args = parser.parse_args()
    if args.rows <= 0 or args.payload_bytes < 0 or args.rounds < 7:
        parser.error(
            "rows must be positive, payload-bytes nonnegative, and rounds at least seven"
        )
    from unstd.dataset import _dataset_native as native

    before = _provenance(native)
    timings: dict[str, dict[str, list[int]]] = {}
    native_calls: dict[str, dict[str, dict[str, int]]] = {}
    orders = []
    with TemporaryDirectory(prefix="dataset-access-") as work:
        directory = Path(work)
        source, mapped_path, sqlite_path = (
            directory / name for name in ("rows.jsonl", "rows.dataset", "rows.sqlite3")
        )
        with source.open("wb") as stream:
            for index in range(args.rows):
                stream.write(
                    json.dumps(
                        _row(index, args.payload_bytes),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )
        builders = {
            "sqlite": lambda: _sqlite_build(sqlite_path, source),
            "mapped": lambda: _mapped_build(mapped_path, source),
        }
        build_samples: dict[str, list[int]] = {name: [] for name in builders}
        for round_index in range(args.rounds):
            for name in (
                tuple(builders) if round_index % 2 == 0 else tuple(reversed(builders))
            ):
                start = time.perf_counter_ns()
                count = builders[name]()
                elapsed = time.perf_counter_ns() - start
                if count != args.rows:
                    message = "builder returned an incorrect row count"
                    raise AssertionError(message)
                build_samples[name].append(elapsed)
        scalar = tuple(
            random.Random(1729).sample(range(args.rows), min(args.rows, 1024))
        )
        small = tuple(random.Random(1730).sample(range(args.rows), min(args.rows, 64)))
        large = tuple(
            random.Random(1731).sample(range(args.rows), min(args.rows, 4096))
        )
        expected_scan: Json = {
            "rows": args.rows,
            "id_sum": args.rows * (args.rows - 1) // 2,
            "group_sum": sum(index % 17 for index in range(args.rows)),
            "text_chars": args.rows * args.payload_bytes,
        }
        expected: dict[str, Json] = {
            "open-count": args.rows,
            "index-1024": [_row(index, args.payload_bytes) for index in scalar],
            "take-64": [_row(index, args.payload_bytes) for index in small],
            "take-4096": [_row(index, args.payload_bytes) for index in large],
            "scan": expected_scan,
            "batches-1024": expected_scan,
        }
        with (
            closing(_SqliteRows(sqlite_path)) as sql,
            _open_mapped(mapped_path, native, False) as portable,
            _open_mapped(mapped_path, native, True) as accelerated,
        ):
            with _backend(native, False):
                portable.verify()
            with _backend(native, True):
                accelerated.verify()
            handles: dict[str, _Rows] = {
                "sqlite": sql,
                "python": portable,
                "native": accelerated,
            }

            def operation(lane: str, name: str) -> Json:
                rows = handles[lane]
                if name == "open-count":
                    if lane == "sqlite":
                        with closing(_SqliteRows(sqlite_path)) as opened:
                            return len(opened)
                    with Dataset(mapped_path) as opened_mapped:
                        return len(opened_mapped)
                if name == "index-1024":
                    return [rows[index] for index in scalar]
                if name == "take-64":
                    return rows.take(small)
                if name == "take-4096":
                    return rows.take(large)
                if name == "scan":
                    return _aggregate(rows)
                return _aggregate(
                    value for batch in rows.batches(1024) for value in batch
                )

            lanes = tuple(handles)
            for name in _READS:
                timings[name] = {lane: [] for lane in lanes}
                native_calls[name] = {}
                for lane in lanes:
                    with _backend(native, lane == "native"), _observe(native) as calls:
                        _checked(
                            partial(operation, lane, name),
                            expected[name],
                        )
                    native_calls[name][lane] = dict(calls)
                    if lane == "native" and not calls:
                        message = "native qualification never reached a C entrypoint"
                        raise AssertionError(message)
                    if lane != "native" and calls:
                        message = "portable or reference control unexpectedly called native dataset code"
                        raise AssertionError(message)
            for round_index in range(args.rounds):
                shift = round_index % len(lanes)
                order = lanes[shift:] + lanes[:shift]
                if round_index % 2:
                    order = tuple(reversed(order))
                orders.append(order)
                for name in _READS:
                    for lane in order:
                        with _backend(native, lane == "native"):
                            start = time.perf_counter_ns()
                            value = operation(lane, name)
                            elapsed = time.perf_counter_ns() - start
                        if value != expected[name]:
                            message = (
                                "timed dataset output differs from its expected result"
                            )
                            raise AssertionError(message)
                        timings[name][lane].append(elapsed)
            for name in _READS:
                for lane in lanes:
                    with _backend(native, lane == "native"):
                        _checked(
                            partial(operation, lane, name),
                            expected[name],
                        )
        storage_sizes = {
            "jsonl_bytes": source.stat().st_size,
            "sqlite_bytes": sqlite_path.stat().st_size,
            "mapped_bytes": mapped_path.stat().st_size,
        }
    after = _provenance(native)
    if after != before:
        message = "product or benchmark source changed during qualification; refusing evidence"
        raise RuntimeError(message)
    report = {
        "schema": 1,
        "recorded": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "sqlite": sqlite3.sqlite_version,
        },
        "provenance": after,
        "fixture": {
            "rows": args.rows,
            "payload_bytes": args.payload_bytes,
            "scalar_indices": len(scalar),
            "small_selection": len(small),
            "large_selection": len(large),
        },
        "rounds": args.rounds,
        "kernel_version": native.KERNEL_VERSION,
        "lane_orders": orders,
        "storage": storage_sizes,
        "build_samples_ns": build_samples,
        "build_median_ns": {
            lane: median(samples) for lane, samples in build_samples.items()
        },
        "samples_ns": timings,
        "median_ns": {
            name: {lane: median(samples) for lane, samples in values.items()}
            for name, values in timings.items()
        },
        "native_calls": native_calls,
        "verified": True,
        "limits": [
            "One machine, warm filesystem cache, deterministic synthetic JSON records; no universal speed claim.",
            "The SQLite reference is self-contained and uses the same JSON encoder and decoder family, including bulk NDJSON for its batch scan.",
            "Builds include source decoding, serialization, disk index construction, file synchronization and atomic publication.",
            "Indexed/scanning operations reuse open handles; open-count measures constructor/count/close separately.",
            "Expected-value comparison, native binding observation, source hashing and fixture creation occur outside timing.",
            "No tracer runs during timing. Raw samples and every matched lane order are retained; ratios can show regressions.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "median_ns": report["median_ns"],
                "build_median_ns": report["build_median_ns"],
                "storage": storage_sizes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _main()
