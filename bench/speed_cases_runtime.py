"""Checked latency workloads for clocks, identity, randomness, and runtime types."""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import partial
import hashlib
import ssl
from typing import TYPE_CHECKING, Any
from uuid import UUID

from bench.speeds import Case, expect


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import ExitStack


EXCLUSIONS = {
    "unstd.time.zoned.Instant": "Imported annotation vocabulary; zoned.instant measures its construction.",
    "unstd.time.zoned.PlainDateTime": "Imported annotation vocabulary; zoned.parse measures its construction.",
    "unstd.time.zoned.ZonedDateTime": "Imported annotation vocabulary; zoned.wall measures its construction.",
}


def cases(stack: ExitStack) -> list[Case]:
    """Build bounded workloads and register restoration of mutable PRNG state."""
    return [*_crypto(), *_random(stack), *_time(), *_identity(), *_types()]


def _crypto() -> list[Case]:
    from unstd.crypto import digest, tls, token

    # Published BLAKE3 reference input and outputs, independent of these wrappers.
    data = bytes(i % 251 for i in range(1024))
    key = b"whats the Elvish word for friend"
    context = "BLAKE3 2019-12-27 16:29:52 test vectors context"
    hashed = bytes.fromhex(
        "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7"
    )
    keyed = bytes.fromhex(
        "75c46f6f3d9eb4f55ecaaee480db732e6c2105546f1e675003687c31719c7ba4"
    )
    derived = bytes.fromhex(
        "7356cd7720d5b66b6d0697eb3177d9f8d73a4a5c5e968896eb6a689684302706"
    )
    stream = digest.Stream((data,))
    claims = b'{"sub":"u1","exp":1767225600,"aud":"example"}'
    claims_key = bytes(range(1, 33))
    bearer = (
        "eyJzdWIiOiJ1MSIsImV4cCI6MTc2NzIyNTYwMCwiYXVkIjoiZXhhbXBsZSJ9"
        ".UM4aL4802CKbmyfa1vlnQ9Gs0A-zvS43YFLx7OTWViw"
    )

    def check_stream(value: Any) -> None:
        assert type(value) is digest.Stream
        assert value.raw() == hashed

    def updated_stream() -> digest.Stream:
        return digest.Stream().update(data)

    def check_tls(value: Any) -> None:
        assert isinstance(value, ssl.SSLContext)
        assert value.check_hostname
        assert value.verify_mode == ssl.CERT_REQUIRED
        assert value.minimum_version == ssl.TLSVersion.TLSv1_3
        assert value.cert_store_stats()["x509_ca"] > 0

    return [
        Case(
            "unstd.crypto.digest.raw",
            partial(digest.raw, data),
            expect(hashed),
            "1 KiB published reference input",
        ),
        Case(
            "unstd.crypto.digest.hex",
            partial(digest.hex, data),
            expect(hashed.hex()),
            "1 KiB published reference input",
        ),
        Case(
            "unstd.crypto.digest.hex#truncated",
            partial(digest.hex, data, 8),
            expect(hashed[:8].hex()),
            "1 KiB input, 8-byte truncation",
        ),
        Case(
            "unstd.crypto.digest.derive_key",
            partial(digest.derive_key, context, data),
            expect(derived),
            "1 KiB published reference input and context",
        ),
        Case(
            "unstd.crypto.digest.legacy_blake2b",
            partial(digest.legacy_blake2b, data, width=32),
            expect(hashlib.blake2b(data, digest_size=32).digest()),
            "1 KiB input, 32-byte BLAKE2b",
        ),
        Case(
            "unstd.crypto.digest.Stream",
            partial(digest.Stream, (data[:512], data[512:])),
            check_stream,
            "construct and absorb two 512-byte chunks",
        ),
        Case(
            "unstd.crypto.digest.Stream#batched",
            partial(digest.Stream, tuple(data[i : i + 32] for i in range(0, 1024, 32))),
            check_stream,
            "construct and absorb 32 chunks of 32 bytes through native batching",
        ),
        Case(
            "unstd.crypto.digest.Stream.update",
            updated_stream,
            check_stream,
            "construct an empty stream and update with 1 KiB",
        ),
        Case(
            "unstd.crypto.digest.Stream.raw",
            stream.raw,
            expect(hashed),
            "finalize a prepared 1 KiB stream",
        ),
        Case(
            "unstd.crypto.digest.Stream.hex",
            stream.hex,
            expect(hashed.hex()),
            "finalize a prepared 1 KiB stream",
        ),
        Case(
            "unstd.crypto.token.equal",
            partial(token.equal, keyed, keyed),
            expect(True),
            "compare equal 32-byte MACs",
        ),
        Case(
            "unstd.crypto.token.mac_raw",
            partial(token.mac_raw, key, data),
            expect(keyed),
            "1 KiB published keyed-hash reference input",
        ),
        Case(
            "unstd.crypto.token.mac_hex",
            partial(token.mac_hex, key, data),
            expect(keyed.hex()),
            "1 KiB published keyed-hash reference input",
        ),
        Case(
            "unstd.crypto.token.verify_mac",
            partial(token.verify_mac, key, data, keyed),
            expect(True),
            "authenticate a valid 1 KiB reference input",
        ),
        Case(
            "unstd.crypto.token.mint",
            partial(token.mint, claims_key, claims),
            expect(bearer),
            "42-byte claims envelope with frozen wire answer",
        ),
        Case(
            "unstd.crypto.token.verify",
            partial(token.verify, claims_key, bearer),
            expect(claims),
            "authenticate and decode the frozen claims envelope",
        ),
        Case(
            "unstd.crypto.token.verify#invalid",
            partial(token.verify, claims_key, bearer[:-1] + "x"),
            expect(None),
            "reject a claims envelope with a changed MAC",
        ),
        Case(
            "unstd.crypto.tls.client_context",
            tls.client_context,
            check_tls,
            "construct context and load the host default CA trust store",
        ),
    ]


def _random(stack: ExitStack) -> list[Case]:
    import numpy as np

    from unstd import rand
    from unstd.rand import crypto, draw

    py_state, np_engine = draw._py.getstate(), draw._gen
    stack.callback(draw._py.setstate, py_state)
    stack.callback(setattr, draw, "_gen", np_engine)
    rand.seed(173)
    gen = rand.Generator(173)
    numpy_gen = rand.Generator(173, backend="numpy")
    population = tuple(range(32))

    def check_generator(value: Any) -> None:
        assert type(value) is rand.Generator
        assert value.backend == "stdlib"
        assert value.random() == 0.3241267240578728

    def float_scalar(value: Any) -> None:
        assert type(value) is float
        assert 0 <= value <= 1

    def int_scalar(value: Any) -> None:
        assert type(value) is int
        assert 0 <= value < 32

    def float_array(value: Any) -> None:
        assert isinstance(value, list | np.ndarray)
        assert len(value) == 256
        assert all(0 <= x <= 1 for x in value)

    def int_array(value: Any) -> None:
        assert isinstance(value, list | np.ndarray)
        assert len(value) == 256
        assert all(0 <= x < 32 and isinstance(x, int | np.integer) for x in value)

    def choice_array(value: Any) -> None:
        assert type(value) is list
        assert len(value) == 256
        assert all(type(x) is int and 0 <= x < 32 for x in value)

    def sample_array(value: Any) -> None:
        assert type(value) is list
        assert len(value) == 8
        assert len(set(value)) == 8
        assert all(type(x) is int and 0 <= x < 32 for x in value)

    def shuffled(value: Any) -> None:
        assert type(value) is list
        assert sorted(value) == list(population)

    def shuffle_module() -> list[int]:
        result = list(population)
        rand.shuffle(result)
        return result

    def shuffle_instance() -> list[int]:
        result = list(population)
        gen.shuffle(result)
        return result

    def bytes_token(value: Any) -> None:
        assert type(value) is bytes
        assert len(value) == 32

    def hex_token(value: Any) -> None:
        assert type(value) is str
        assert len(value) == 64
        assert len(bytes.fromhex(value)) == 32

    def url_token(value: Any) -> None:
        assert type(value) is str
        assert len(value) == 43
        assert all(c.isascii() and (c.isalnum() or c in "-_") for c in value)

    def bits(value: Any) -> None:
        assert type(value) is int
        assert 0 <= value < (1 << 64)

    module_specs: list[tuple[str, Callable[[], object], Callable[[Any], None], str]] = [
        (
            "seed",
            partial(rand.seed, 173),
            expect(None),
            "seed both module engines with integer 173",
        ),
        ("random", rand.random, float_scalar, "one scalar draw"),
        (
            "randint",
            partial(rand.randint, 0, 31),
            int_scalar,
            "one scalar integer in [0, 31]",
        ),
        (
            "uniform",
            partial(rand.uniform, 0.0, 1.0),
            float_scalar,
            "one scalar uniform draw",
        ),
        (
            "choice",
            partial(rand.choice, population),
            int_scalar,
            "one choice from 32 integers",
        ),
        ("shuffle", shuffle_module, shuffled, "copy and shuffle a 32-integer list"),
        (
            "sample",
            partial(rand.sample, population, 8),
            sample_array,
            "8 distinct draws from 32 integers",
        ),
        ("floats", partial(rand.floats, 256), float_array, "256 NumPy float draws"),
        ("ints", partial(rand.ints, 256, 0, 32), int_array, "256 NumPy integer draws"),
        (
            "choices",
            partial(rand.choices, population, 256),
            choice_array,
            "256 choices from 32 integers using NumPy",
        ),
    ]
    instance_specs: list[
        tuple[str, Callable[[], object], Callable[[Any], None], str]
    ] = [
        (
            "seed",
            partial(gen.seed, 173),
            expect(None),
            "seed the independent stdlib engine with 173",
        ),
        ("random", gen.random, float_scalar, "one scalar stdlib draw"),
        (
            "randint",
            partial(gen.randint, 0, 31),
            int_scalar,
            "one scalar stdlib integer",
        ),
        (
            "uniform",
            partial(gen.uniform, 0.0, 1.0),
            float_scalar,
            "one scalar stdlib uniform draw",
        ),
        (
            "choice",
            partial(gen.choice, population),
            int_scalar,
            "one stdlib choice from 32 integers",
        ),
        (
            "shuffle",
            shuffle_instance,
            shuffled,
            "copy and shuffle 32 integers using the independent engine",
        ),
        (
            "sample",
            partial(gen.sample, population, 8),
            sample_array,
            "8 distinct stdlib draws from 32 integers",
        ),
        (
            "floats",
            partial(gen.floats, 256),
            float_array,
            "256 floats from the independent stdlib engine",
        ),
        (
            "ints",
            partial(gen.ints, 256, 0, 32),
            int_array,
            "256 integers from the independent stdlib engine",
        ),
        (
            "choices",
            partial(gen.choices, population, 256),
            choice_array,
            "256 choices from the independent stdlib engine",
        ),
    ]
    return [
        *[
            Case(f"unstd.rand.{name}", run, check, note)
            for name, run, check, note in module_specs
        ],
        Case(
            "unstd.rand.Generator",
            partial(rand.Generator, 173),
            check_generator,
            "construct an independent stdlib engine with seed 173",
        ),
        Case(
            "unstd.rand.Generator.backend",
            lambda: gen.backend,
            expect("stdlib"),
            "read the prepared generator's explicitly selected backend",
        ),
        *[
            Case(f"unstd.rand.Generator.{name}", run, check, note)
            for name, run, check, note in instance_specs
        ],
        Case(
            "unstd.rand.Generator.floats#numpy",
            partial(numpy_gen.floats, 256),
            float_array,
            "256 floats from an independent NumPy engine",
        ),
        Case(
            "unstd.rand.Generator.ints#numpy",
            partial(numpy_gen.ints, 256, 0, 32),
            int_array,
            "256 integers from an independent NumPy engine",
        ),
        Case(
            "unstd.rand.Generator.choices#numpy",
            partial(numpy_gen.choices, population, 256),
            choice_array,
            "256 choices from an independent NumPy engine",
        ),
        Case(
            "unstd.rand.crypto.below",
            partial(crypto.below, 32),
            int_scalar,
            "one OS-entropy integer below 32",
        ),
        Case(
            "unstd.rand.crypto.bits",
            partial(crypto.bits, 64),
            bits,
            "64 OS-entropy bits",
        ),
        Case(
            "unstd.rand.crypto.choice",
            partial(crypto.choice, population),
            int_scalar,
            "one secure choice from 32 integers",
        ),
        Case(
            "unstd.rand.crypto.token_bytes",
            partial(crypto.token_bytes, 32),
            bytes_token,
            "32 OS-entropy bytes",
        ),
        Case(
            "unstd.rand.crypto.token_hex",
            partial(crypto.token_hex, 32),
            hex_token,
            "32 OS-entropy bytes encoded as hex",
        ),
        Case(
            "unstd.rand.crypto.token_urlsafe",
            partial(crypto.token_urlsafe, 32),
            url_token,
            "32 OS-entropy bytes encoded as unpadded base64url",
        ),
    ]


def _time() -> list[Case]:
    from unstd.time import timeutil, zoned

    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    raw = "2026-01-01T00:00:00Z"
    epoch = 1767225600
    prepared_zoned = zoned.wall(2026, 1, 1, 0, 0, "UTC")
    assert prepared_zoned is not None
    started = timeutil.perf()

    def float_clock(value: Any) -> None:
        assert type(value) is float
        assert value > 0

    def int_clock(value: Any) -> None:
        assert type(value) is int
        assert value > 0

    def epoch_seconds(value: Any) -> None:
        assert type(value) is int
        assert abs(value - datetime.now(UTC).timestamp()) < 60

    def epoch_milliseconds(value: Any) -> None:
        assert type(value) is int
        assert abs(value / 1000 - datetime.now(UTC).timestamp()) < 60

    def utc_datetime(value: Any) -> None:
        assert type(value) is datetime
        assert value.tzinfo is UTC
        assert abs((value - datetime.now(UTC)).total_seconds()) < 60

    def today(value: Any) -> None:
        assert type(value) is date
        assert abs((value - datetime.now(UTC).date()).days) <= 1

    def human(value: Any) -> None:
        assert type(value) is str
        assert " at " in value
        assert value.endswith(" UTC")

    def proto(value: Any) -> None:
        from google.protobuf.timestamp_pb2 import Timestamp

        assert type(value) is Timestamp
        assert value.seconds == epoch
        assert value.nanos == 0

    def instant(value: Any) -> None:
        assert type(value) is zoned.Instant
        assert value.to_stdlib() == stamp

    def wall(value: Any) -> None:
        assert type(value) is zoned.ZonedDateTime
        assert value.tz == "UTC"
        assert value.to_instant().to_stdlib() == stamp

    def now(value: Any) -> None:
        assert type(value) is zoned.ZonedDateTime
        assert value.tz == "UTC"
        assert (
            abs((value.to_instant().to_stdlib() - datetime.now(UTC)).total_seconds())
            < 60
        )

    return [
        Case(
            "unstd.time.timeutil.wall",
            timeutil.wall,
            float_clock,
            "one wall-clock read",
        ),
        Case(
            "unstd.time.timeutil.mono",
            timeutil.mono,
            float_clock,
            "one monotonic-clock read",
        ),
        Case(
            "unstd.time.timeutil.mono_ns",
            timeutil.mono_ns,
            int_clock,
            "one integer monotonic-clock read",
        ),
        Case(
            "unstd.time.timeutil.perf",
            timeutil.perf,
            float_clock,
            "one performance-counter read",
        ),
        Case(
            "unstd.time.timeutil.elapsed_ms",
            partial(timeutil.elapsed_ms, started),
            float_clock,
            "elapsed milliseconds from a prepared performance-counter value",
        ),
        Case(
            "unstd.time.timeutil.epoch_s",
            timeutil.epoch_s,
            epoch_seconds,
            "one whole-seconds epoch read",
        ),
        Case(
            "unstd.time.timeutil.epoch_ms",
            timeutil.epoch_ms,
            epoch_milliseconds,
            "one integer-milliseconds epoch read",
        ),
        Case(
            "unstd.time.timeutil.utcnow",
            timeutil.utcnow,
            utc_datetime,
            "current aware UTC datetime",
        ),
        Case(
            "unstd.time.timeutil.now_tz",
            partial(timeutil.now_tz, UTC),
            utc_datetime,
            "current aware datetime in explicit UTC",
        ),
        Case(
            "unstd.time.timeutil.today",
            partial(timeutil.today, utc=True),
            today,
            "current date in explicit UTC",
        ),
        Case(
            "unstd.time.timeutil.iso",
            partial(timeutil.iso, stamp),
            expect(raw),
            "format a fixed aware UTC datetime at seconds precision",
        ),
        Case(
            "unstd.time.timeutil.human",
            partial(timeutil.human, "UTC"),
            human,
            "current human-readable time in explicit UTC",
        ),
        Case(
            "unstd.time.timeutil.parse_iso",
            partial(timeutil.parse_iso, raw),
            expect(stamp),
            "parse one RFC-3339 UTC timestamp",
        ),
        Case(
            "unstd.time.timeutil.parse_utc",
            partial(timeutil.parse_utc, raw),
            expect(stamp),
            "parse and normalize one RFC-3339 timestamp",
        ),
        Case(
            "unstd.time.timeutil.parse_date",
            partial(timeutil.parse_date, "2026-01-01"),
            expect(stamp.date()),
            "parse one ISO calendar date",
        ),
        Case(
            "unstd.time.timeutil.from_epoch_s",
            partial(timeutil.from_epoch_s, epoch),
            expect(stamp),
            "convert fixed whole epoch seconds to UTC",
        ),
        Case(
            "unstd.time.timeutil.from_epoch_ms",
            partial(timeutil.from_epoch_ms, epoch * 1000),
            expect(stamp),
            "convert fixed integer epoch milliseconds to UTC",
        ),
        Case(
            "unstd.time.timeutil.span",
            partial(timeutil.span, raw, "2026-01-01T01:00:00Z", unit="minutes"),
            expect(60.0),
            "duration between two RFC-3339 timestamps in minutes",
        ),
        Case(
            "unstd.time.timeutil.proto_timestamp",
            partial(timeutil.proto_timestamp, raw),
            proto,
            "parse a fixed RFC-3339 timestamp and construct a protobuf Timestamp",
        ),
        Case(
            "unstd.time.timeutil.proto_timestamp_ms",
            partial(timeutil.proto_timestamp_ms, epoch * 1000),
            proto,
            "construct a protobuf Timestamp from fixed integer milliseconds",
        ),
        Case(
            "unstd.time.zoned.instant",
            partial(zoned.instant, raw),
            instant,
            "parse one UTC timestamp into a whenever Instant",
        ),
        Case(
            "unstd.time.zoned.now",
            partial(zoned.now, "UTC"),
            now,
            "read the current instant in explicit UTC",
        ),
        Case(
            "unstd.time.zoned.parse",
            partial(zoned.parse, "2026-01-01T00:00:00", "UTC"),
            wall,
            "resolve one floating timestamp in UTC",
        ),
        Case(
            "unstd.time.zoned.wall",
            partial(zoned.wall, 2026, 1, 1, 0, 0, "UTC"),
            wall,
            "construct one local wall time in UTC",
        ),
        Case(
            "unstd.time.zoned.to_utc",
            partial(zoned.to_utc, prepared_zoned),
            expect(stamp),
            "convert one prepared ZonedDateTime to stdlib UTC",
        ),
    ]


def _identity() -> list[Case]:
    from unstd.ids import uid

    namespace = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    canonical = "886313e1-3b8a-5372-9b90-0c9aee199e5d"

    def uuid7(value: Any) -> None:
        assert type(value) is str
        parsed = UUID(value)
        assert parsed.version == 7
        assert parsed.variant == "specified in RFC 4122"
        assert len(value) in (32, 36)
        assert value == value.lower()

    def canonical_uuid7(value: Any) -> None:
        uuid7(value)
        assert len(value) == 36
        assert str(UUID(value)) == value

    def hex_uuid7(value: Any) -> None:
        uuid7(value)
        assert len(value) == 32
        assert UUID(value).hex == value

    return [
        Case(
            "unstd.ids.uid.new",
            uid.new,
            canonical_uuid7,
            "mint and format one native UUIDv7",
        ),
        Case(
            "unstd.ids.uid.new_hex",
            uid.new_hex,
            hex_uuid7,
            "mint one native UUIDv7 as 32 hex digits",
        ),
        Case(
            "unstd.ids.uid.parse",
            partial(uid.parse, canonical),
            expect(UUID(canonical)),
            "parse one canonical UUID string",
        ),
        Case(
            "unstd.ids.uid.derive",
            partial(uid.derive, namespace, "python.org"),
            expect(UUID(canonical)),
            "RFC 4122 DNS namespace and python.org golden UUIDv5",
        ),
    ]


def _types() -> list[Case]:
    from unstd import containers, model

    class Wire(model.StrictModel):
        number: int
        label: str

    class FrozenWire(model.FrozenModel):
        number: int
        label: str

    class Settings(model.StrictSettings):
        unstd_speed_number: int

    def wire(value: Any) -> None:
        assert type(value) is Wire
        assert value.model_dump() == {"number": 7, "label": "speed"}
        assert value.model_config["extra"] == "forbid"

    def frozen(value: Any) -> None:
        assert type(value) is FrozenWire
        assert value.model_dump() == {"number": 7, "label": "speed"}
        assert value.model_config["frozen"] is True
        assert isinstance(hash(value), int)

    def settings(value: Any) -> None:
        assert type(value) is Settings
        assert value.unstd_speed_number == 7
        assert value.model_config["extra"] == "ignore"

    items = tuple(range(127, -1, -1))
    pairs = tuple((i, str(i)) for i in items)

    def sorted_values(value: Any) -> None:
        assert list(value) == list(range(128))
        assert isinstance(
            value, containers.SortedList | containers.SortedSet | containers.SortedDict
        )

    def mapping(value: Any) -> None:
        assert isinstance(value, containers.Map)
        assert dict(value) == dict(pairs)

    sorted_list = containers.SortedList(items)
    sorted_dict = containers.SortedDict(pairs)
    sorted_set = containers.SortedSet(items)
    immutable = containers.Map(pairs)

    def persistent_set(value: Any) -> None:
        assert isinstance(value, containers.Map)
        assert value[128] == "new"
        assert 128 not in immutable
        assert len(value) == 129

    return [
        Case(
            "unstd.model.StrictModel",
            partial(Wire, number=7, label="speed"),
            wire,
            "construct a two-field StrictModel subclass",
        ),
        Case(
            "unstd.model.FrozenModel",
            partial(FrozenWire, number=7, label="speed"),
            frozen,
            "construct a two-field FrozenModel subclass",
        ),
        Case(
            "unstd.model.StrictSettings",
            partial(Settings, unstd_speed_number=7),
            settings,
            "construct a one-field StrictSettings subclass including environment lookup",
        ),
        Case(
            "unstd.containers.SortedList",
            partial(containers.SortedList, items),
            sorted_values,
            "construct a sorted list from 128 descending integers",
        ),
        Case(
            "unstd.containers.SortedSet",
            partial(containers.SortedSet, items),
            sorted_values,
            "construct a sorted set from 128 descending integers",
        ),
        Case(
            "unstd.containers.SortedDict",
            partial(containers.SortedDict, pairs),
            sorted_values,
            "construct a sorted dictionary from 128 descending integer/string pairs",
        ),
        Case(
            "unstd.containers.Map",
            partial(containers.Map, pairs),
            mapping,
            "construct a persistent map from 128 integer/string pairs",
        ),
        Case(
            "unstd.containers.SortedList#bisect",
            partial(sorted_list.bisect_left, 64),
            expect(64),
            "bisect a prepared 128-integer sorted list",
        ),
        Case(
            "unstd.containers.SortedSet#contains",
            partial(sorted_set.__contains__, 64),
            expect(True),
            "membership in a prepared 128-integer sorted set",
        ),
        Case(
            "unstd.containers.SortedDict#get",
            partial(sorted_dict.__getitem__, 64),
            expect("64"),
            "lookup in a prepared 128-entry sorted dictionary",
        ),
        Case(
            "unstd.containers.Map#set",
            partial(immutable.set, 128, "new"),
            persistent_set,
            "persistent update to a prepared 128-entry map",
        ),
    ]
