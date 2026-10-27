"""Persistent JSON results, with concurrent fills shared across processes.

``Cache`` composes :mod:`unstd.serde.jsonx` with stdlib SQLite. Each operation
owns and closes its connection, so a handle can be shared by threads or reused
after a fork. See the package guide for expiry, eviction and writer contention.
"""

from __future__ import annotations

from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING, Any, Protocol, cast, overload

from unstd import fs
from unstd.cache import locking
from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from os import PathLike

    from unstd.serde.jsonx import Json


__all__ = ["Cache"]

_MISSING = object()


class _MethodCodec[T](Protocol):
    """A validating binary codec implemented with ordinary bound methods."""

    def encode(self, value: T, /) -> bytes:
        """One value as bytes suitable for persistent storage."""

    def decode(self, raw: bytes, /) -> T:
        """One validated value, rejecting malformed or incompatible bytes."""


class _CallableCodec[T](Protocol):
    """The paired callable attributes used by reusable codec implementations."""

    encode: Callable[[T], bytes]
    decode: Callable[[bytes], T]


type _Codec[T] = _MethodCodec[T] | _CallableCodec[T]


def _seconds(value: float, name: str) -> float:
    """A finite, nonnegative duration, rejecting booleans and strings."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name} must be a finite nonnegative number"
        raise TypeError(msg)
    try:
        result = float(value)
    except OverflowError as error:
        msg = f"{name} must be finite and nonnegative"
        raise ValueError(msg) from error
    if not math.isfinite(result) or result < 0:
        msg = f"{name} must be finite and nonnegative"
        raise ValueError(msg)
    return result


def _text(value: str, name: str) -> None:
    """SQLite names and keys are UTF-8 strings, never SQL fragments."""
    if not isinstance(value, str):
        msg = f"{name} must be a string"
        raise TypeError(msg)
    value.encode("utf-8")


def _encode(value: Json) -> bytes:
    """Portable JSON with finite numbers and escaped lone surrogates."""
    return jsonx.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


class Cache:
    """A namespaced, disk-backed JSON cache with optional TTL and FIFO capacity.

    The parent directory is created as needed. New database files request mode
    ``0o600``; existing permissions are retained. Use a trusted local directory
    on a filesystem supporting SQLite WAL. Connections use full synchronization.
    ``timeout`` bounds each file-lock acquisition phase and each SQLite lock
    wait, not the duration of a producer or the complete operation.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        namespace: str = "default",
        timeout: float = 30.0,
        max_entries: int | None = None,
    ) -> None:
        """Initialize a persistent cache without retaining a connection."""
        _text(namespace, "namespace")
        self._timeout = _seconds(timeout, "timeout")
        if max_entries is not None and (
            type(max_entries) is not int or max_entries <= 0
        ):
            msg = "max_entries must be a positive integer or None"
            raise ValueError(msg)
        self._target = locking.prepare(Path(path))
        self._path = self._target.path
        self._namespace = namespace
        # SQLite's OFFSET accepts a signed 64-bit integer; larger capacities
        # are indistinguishable because SQLite cannot store that many rows.
        self._max_entries = (
            None if max_entries is None else min(max_entries, (1 << 63) - 1)
        )
        fs.ensure_dir(self._path.parent)
        try:
            descriptor = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with closing(self._connect()) as connection:
            # Reopening a populated cache must not contend with a slow fill.
            # Schema creation below is atomic, so a visible table is complete.
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = '_unstd_cache_v1'"
            ).fetchone()
            mode = connection.execute("PRAGMA journal_mode").fetchone()
            if exists is not None and mode == ("wal",):
                return
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode != ("wal",):
                msg = "cache requires SQLite WAL journal mode"
                raise sqlite3.OperationalError(msg)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS _unstd_cache_v1 ("
                    "namespace TEXT NOT NULL, key TEXT NOT NULL, value BLOB NOT NULL, "
                    "expires_at REAL, written_at INTEGER NOT NULL, "
                    "PRIMARY KEY (namespace, key))"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS _unstd_cache_v1_expiry "
                    "ON _unstd_cache_v1 (namespace, expires_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS _unstd_cache_v1_order "
                    "ON _unstd_cache_v1 (namespace, written_at)"
                )

    def _connect(self) -> sqlite3.Connection:
        """An autocommit connection whose owner must close it."""
        connection = sqlite3.connect(
            self._path, timeout=self._timeout, isolation_level=None
        )
        try:
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _lookup(
        self,
        connection: sqlite3.Connection,
        key: str,
        decode: Callable[[bytes], object] | None = None,
    ) -> object:
        """An independently decoded value, or the private absence sentinel."""
        row = connection.execute(
            "SELECT value FROM _unstd_cache_v1 WHERE namespace = ? AND key = ? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (self._namespace, key, time.time()),
        ).fetchone()
        if row is None:
            return _MISSING
        if decode is None:
            return jsonx.loads(row[0])
        raw = row[0]
        if not isinstance(raw, bytes):
            msg = "cached codec artifact is not bytes"
            raise TypeError(msg)
        return decode(raw)

    def _write(
        self,
        connection: sqlite3.Connection,
        payloads: list[tuple[str, bytes]],
        ttl: float | None,
    ) -> None:
        """Publish a prepared batch with one expiry time and FIFO allocation."""
        now = time.time()
        expires_at = None if ttl is None else now + ttl
        if expires_at is not None and not math.isfinite(expires_at):
            msg = "ttl expiry is outside the finite timestamp range"
            raise ValueError(msg)
        # A transactionally assigned sequence preserves FIFO under clock changes.
        first_order = connection.execute(
            "SELECT COALESCE(MAX(written_at), 0) + 1 FROM _unstd_cache_v1 "
            "WHERE namespace = ?",
            (self._namespace,),
        ).fetchone()[0]
        connection.executemany(
            "INSERT INTO _unstd_cache_v1 "
            "(namespace, key, value, expires_at, written_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET "
            "value = excluded.value, expires_at = excluded.expires_at, "
            "written_at = excluded.written_at",
            (
                (self._namespace, key, payload, expires_at, first_order + offset)
                for offset, (key, payload) in enumerate(payloads)
            ),
        )
        self._retain(connection, now)

    def _retain(self, connection: sqlite3.Connection, now: float) -> None:
        """Apply namespace expiry and FIFO capacity once after publication."""
        # Include the new row: an immediate expiry must not evict live results.
        connection.execute(
            "DELETE FROM _unstd_cache_v1 WHERE namespace = ? AND expires_at <= ?",
            (self._namespace, now),
        )
        if self._max_entries is not None:
            connection.execute(
                "DELETE FROM _unstd_cache_v1 WHERE namespace = ? AND key IN "
                "(SELECT key FROM _unstd_cache_v1 WHERE namespace = ? "
                "ORDER BY written_at DESC LIMIT -1 OFFSET ?)",
                (self._namespace, self._namespace, self._max_entries),
            )

    def _store(
        self,
        connection: sqlite3.Connection,
        key: str,
        payload: bytes,
        ttl: float | None,
    ) -> None:
        """Publish one prepared value inside an owned write transaction."""
        self._write(connection, [(key, payload)], ttl)

    def get_many(self, keys: Iterable[str]) -> dict[str, Json]:
        """The live values for *keys*, read from one consistent database snapshot.

        Missing and expired keys are omitted, while a cached JSON null remains
        present with value ``None``. Duplicate keys collapse to their first input
        position. All keys are validated before opening a connection. Queries
        are chunked within SQLite's parameter limit and share one expiry time;
        returned mutable values are independent decoded JSON.
        """
        requested = list(keys)
        for key in requested:
            _text(key, "key")
        requested = list(dict.fromkeys(requested))
        if not requested:
            return {}
        found: dict[str, Json] = {}
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            now = time.time()
            chunk_size = min(
                900, connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) - 2
            )
            if chunk_size < 1:
                msg = "SQLite variable limit is too small for a cache lookup"
                raise sqlite3.OperationalError(msg)
            for start in range(0, len(requested), chunk_size):
                chunk = requested[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                # Only placeholder count enters SQL; all caller values are bound.
                statement = (
                    "SELECT key, value FROM _unstd_cache_v1 "  # noqa: S608
                    "WHERE namespace = ? AND key IN ("
                    + placeholders
                    + ") AND (expires_at IS NULL OR expires_at > ?)"
                )
                for key, payload in connection.execute(
                    statement, (self._namespace, *chunk, now)
                ):
                    found[key] = jsonx.loads(payload)
        return {key: found[key] for key in requested if key in found}

    def set_many(self, values: Mapping[str, Json], *, ttl: float | None = None) -> None:
        """Atomically replace a mapping of JSON values with one committed write.

        All keys and values are prepared before acquiring locks. A validation,
        encoding, or database failure leaves the entire batch unpublished. TTL
        starts after encoding and lock acquisition and is shared by every value.
        Mapping order determines FIFO write order; expiry cleanup and capacity
        enforcement run once for the batch. Empty mappings perform no I/O.
        """
        locking.check_writable(self._target)
        duration = None if ttl is None else _seconds(ttl, "ttl")
        entries = list(values.items())
        for key, _ in entries:
            _text(key, "key")
        payloads = [(key, _encode(value)) for key, value in entries]
        if not payloads:
            return
        with (
            locking.locked(
                self._target,
                self._namespace,
                (key for key, _ in payloads),
                self._timeout,
            ),
            closing(self._connect()) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            self._write(connection, payloads, duration)

    def get(self, key: str, default: Any = None) -> Any:
        """The decoded live value, or *default* when absent or expired.

        ``None`` is a cacheable value; pass a sentinel as *default* to distinguish
        it from absence. Malformed stored JSON raises instead of becoming a miss.
        Reads do not refresh TTL, eviction order or write to the database.
        """
        _text(key, "key")
        with closing(self._connect()) as connection:
            value = self._lookup(connection, key)
        return default if value is _MISSING else value

    def set(self, key: str, value: Json, *, ttl: float | None = None) -> None:
        """Atomically replace *key* with JSON, expiring after *ttl* seconds.

        ``None`` means no expiry; zero expires immediately. TTL starts after
        encoding and lock acquisition. JSON normalization follows stdlib JSON:
        tuple inputs become lists and supported non-string object keys become
        strings. Nonfinite numbers and unserializable values raise before writing.
        """
        _text(key, "key")
        locking.check_writable(self._target)
        duration = None if ttl is None else _seconds(ttl, "ttl")
        payload = _encode(value)
        with (
            locking.locked(self._target, self._namespace, (key,), self._timeout),
            closing(self._connect()) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            self._store(connection, key, payload, duration)

    def delete(self, key: str) -> bool:
        """Remove *key*, returning whether a stored row existed (even if expired)."""
        _text(key, "key")
        locking.check_writable(self._target)
        with (
            locking.locked(self._target, self._namespace, (key,), self._timeout),
            closing(self._connect()) as connection,
        ):
            result = connection.execute(
                "DELETE FROM _unstd_cache_v1 WHERE namespace = ? AND key = ?",
                (self._namespace, key),
            )
            return result.rowcount != 0

    def clear(self) -> int:
        """Remove all stored rows in this namespace, returning their count."""
        locking.check_writable(self._target)
        with (
            locking.locked(self._target, self._namespace, None, self._timeout),
            closing(self._connect()) as connection,
        ):
            result = connection.execute(
                "DELETE FROM _unstd_cache_v1 WHERE namespace = ?", (self._namespace,)
            )
            return result.rowcount

    @overload
    def get_or_compute(
        self,
        key: str,
        factory: Callable[[], Json],
        *,
        ttl: float | None = None,
        codec: None = None,
    ) -> Json: ...

    @overload
    def get_or_compute[T](
        self,
        key: str,
        factory: Callable[[], T],
        *,
        ttl: float | None = None,
        codec: _Codec[T],
    ) -> T: ...

    def get_or_compute[T](
        self,
        key: str,
        factory: Callable[[], T],
        *,
        ttl: float | None = None,
        codec: _Codec[T] | None = None,
    ) -> Json | T:
        """The live result, computing and publishing once on a simultaneous miss.

        A miss takes a bounded key lock and rechecks before calling *factory*.
        Different lock stripes compute concurrently, outside SQLite transactions;
        reads and unrelated writes continue. Same-key callers wait and reuse
        the published result. Factories may read but must not mutate this database
        or start another fill in it. Lock waits beyond ``timeout`` raise
        ``sqlite3.OperationalError``; producer time does not consume this budget.

        The default stores JSON. An explicit structural ``codec`` supplies
        ``encode(value) -> bytes`` and validating ``decode(bytes) -> T`` for
        typed artifacts. Use the same codec and a versioned namespace on every
        access to those keys; JSON CRUD methods do not decode codec artifacts.

        Producer, encoding and cold validation failures publish nothing. TTL
        starts after successful computation and encoding. Both cold and warm
        values pass through the decoder, so codecs own normalization and value
        independence. A crash, expiry, eviction or explicit removal can cause
        later recomputation; external side effects are outside the transaction.
        """
        _text(key, "key")
        duration = None if ttl is None else _seconds(ttl, "ttl")
        if not callable(factory):
            msg = "factory must be callable"
            raise TypeError(msg)
        decode: Callable[[bytes], object] | None = None
        if codec is not None:
            if not callable(codec.encode) or not callable(codec.decode):
                msg = "codec must provide callable encode and decode attributes"
                raise TypeError(msg)
            decode = codec.decode
        # A producer may read a warm dependency; a nested miss could deadlock
        # with another producer acquiring these key locks in the reverse order.
        with closing(self._connect()) as connection:
            value = self._lookup(connection, key, decode)
            if value is not _MISSING:
                return cast("Json | T", value)
        locking.check_writable(self._target)
        with locking.locked(self._target, self._namespace, (key,), self._timeout):
            with closing(self._connect()) as connection, connection:
                # Preflight contention and recheck atomically, then release the
                # writer before doing expensive work. Publication takes a new,
                # short transaction while the same key lock is still held.
                connection.execute("BEGIN IMMEDIATE")
                value = self._lookup(connection, key, decode)
                if value is not _MISSING:
                    return cast("Json | T", value)
            with locking.producing(self._target):
                if codec is None:
                    # The default overload restricts its producer to Json;
                    # the serializer remains the runtime admission boundary.
                    payload = _encode(cast("Json", factory()))
                else:
                    payload = codec.encode(factory())
                    if not isinstance(payload, bytes):
                        msg = "cache codec encode must return bytes"
                        raise TypeError(msg)
                value = jsonx.loads(payload) if decode is None else decode(payload)
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._store(connection, key, payload, duration)
            return cast("Json | T", value)
