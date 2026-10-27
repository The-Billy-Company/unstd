"""Correctness-audited workloads for serialization, packing and audio APIs."""

from __future__ import annotations

import base64
from decimal import Decimal
from functools import partial
import gzip
from importlib import import_module
import io
import json
import struct
from typing import TYPE_CHECKING, Any
import wave
import zlib

from bench.speeds import Case, expect


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import ExitStack


EXCLUSIONS: dict[str, str] = {
    "unstd.serde.structs.DecHook": "Callable typing alias, with no runtime operation.",
    "unstd.serde.structs.EncHook": "Callable typing alias, with no runtime operation.",
    "unstd.serde.structs.FloatHook": "Callable typing alias, with no runtime operation.",
}


def _instance(kind: type) -> Callable[[object], None]:
    def check(value: object) -> None:
        assert isinstance(value, kind), (kind, type(value))

    return check


def _serde_cases(*, comparison: bool = False) -> list[Case]:
    from unstd.serde import b64, jsonx, ndjson, structs

    rows = [{"id": n, "text": "café", "ok": True} for n in range(32)]
    text = json.dumps(rows)
    compact = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    canonical = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    lines = b"".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        for row in rows
    )
    out: list[Case] = []

    def add(
        name: str, run: Callable[[], object], check: Callable[[object], None], note: str
    ) -> None:
        out.append(Case(f"unstd.serde.{name}", run, check, note))

    add("jsonx.dumps", partial(jsonx.dumps, rows), expect(text), "32 Unicode records")
    add(
        "jsonx.dumpb", partial(jsonx.dumpb, rows), expect(compact), "32 Unicode records"
    )
    add("jsonx.loads", partial(jsonx.loads, text), expect(rows), "32 Unicode records")
    add(
        "jsonx.canonical",
        partial(jsonx.canonical, rows),
        expect(canonical),
        "32 Unicode records; sorted ASCII format",
    )
    if not comparison or hasattr(jsonx, "canonical_strict"):
        add(
            "jsonx.canonical_strict",
            partial(jsonx.canonical_strict, rows),
            expect(canonical),
            "32 exact builtin Unicode records; validated sorted ASCII format",
        )

    def json_dump() -> str:
        sink = io.StringIO()
        jsonx.dump(rows, sink)
        return sink.getvalue()

    add("jsonx.dump", json_dump, expect(text), "32 records; new StringIO included")
    add(
        "jsonx.load",
        lambda: jsonx.load(io.StringIO(text)),
        expect(rows),
        "32 records; new StringIO included",
    )
    if not comparison or hasattr(jsonx, "iterload"):
        array_items = jsonx.iterload
        add(
            "jsonx.iterload",
            lambda: list(array_items(io.BytesIO(compact))),
            expect(rows),
            "32 Unicode array records; new BytesIO and full iterator consumption included",
        )
    if not comparison or hasattr(jsonx, "dump_array"):
        array_writer = jsonx.dump_array

        def json_dump_array() -> tuple[int, bytes]:
            sink = io.BytesIO()
            count = array_writer(iter(rows), sink)
            return count, sink.getvalue()

        add(
            "jsonx.dump_array",
            json_dump_array,
            expect((len(rows), compact)),
            "32 Unicode array records; new BytesIO and iterator included",
        )
    add(
        "jsonx.JSONEncoder",
        jsonx.JSONEncoder,
        _instance(json.JSONEncoder),
        "default constructor",
    )
    add(
        "jsonx.JSONDecoder",
        jsonx.JSONDecoder,
        _instance(json.JSONDecoder),
        "default constructor",
    )
    add(
        "ndjson.dumps",
        partial(ndjson.dumps, rows),
        expect(lines.decode()),
        "32 Unicode rows",
    )
    add("ndjson.dumpb", partial(ndjson.dumpb, rows), expect(lines), "32 Unicode rows")
    add("ndjson.loads", partial(ndjson.loads, lines), expect(rows), "32 Unicode rows")
    add(
        "ndjson.iter_loads",
        lambda: list(ndjson.iter_loads(lines.splitlines())),
        expect(rows),
        "32 Unicode rows; split and full iterator consumption included",
    )
    add(
        "ndjson.load",
        lambda: ndjson.load(io.BytesIO(lines)),
        expect(rows),
        "32 Unicode rows; new BytesIO included",
    )

    def ndjson_dump() -> tuple[int, bytes]:
        sink = io.BytesIO()
        count = ndjson.dump(rows, sink)
        return count, sink.getvalue()

    add(
        "ndjson.dump",
        ndjson_dump,
        expect((len(rows), lines)),
        "32 Unicode rows; new BytesIO and returned count included",
    )
    codec = structs.Codec(list[dict[str, Any]])
    add(
        "structs.Codec",
        lambda: structs.Codec(list[dict[str, Any]]),
        _instance(structs.Codec),
        "list-of-dictionaries schema; encoder and decoder construction",
    )
    add(
        "structs.Codec.encode",
        partial(codec.encode, rows),
        expect(compact),
        "reused typed codec; 32 records",
    )
    add(
        "structs.Codec.decode",
        partial(codec.decode, compact),
        expect(rows),
        "reused typed codec; 32 records",
    )
    add(
        "structs.Codec.decode_or",
        partial(codec.decode_or, compact),
        expect(rows),
        "reused typed codec; 32 valid records",
    )
    add(
        "structs.Codec.decode_or#invalid",
        partial(codec.decode_or, b"{"),
        expect(None),
        "reused typed codec; malformed input",
    )
    add(
        "structs.float_enc_hook",
        partial(structs.float_enc_hook, Decimal("1.25")),
        expect(1.25),
        "Decimal numeric scalar",
    )

    raw = b"benchmark payload\x00\xff" * 256
    encoded = base64.b64encode(raw)
    urlsafe = base64.urlsafe_b64encode(raw)
    for name, fn, payload, expected in (
        ("b64encode", b64.b64encode, raw, encoded),
        ("b64decode", b64.b64decode, encoded, raw),
        ("standard_b64encode", b64.standard_b64encode, raw, encoded),
        ("standard_b64decode", b64.standard_b64decode, encoded, raw),
        ("urlsafe_b64encode", b64.urlsafe_b64encode, raw, urlsafe),
        ("urlsafe_b64decode", b64.urlsafe_b64decode, urlsafe, raw),
        ("encodebytes", b64.encodebytes, raw, base64.encodebytes(raw)),
        ("decodebytes", b64.decodebytes, base64.encodebytes(raw), raw),
        ("encode", b64.encode, raw, encoded.decode()),
        ("decode", b64.decode, encoded, raw),
    ):
        add(
            f"b64.{name}",
            partial(fn, payload),
            expect(expected),
            "4864 raw bytes; standard or named alphabet",
        )
    utf8 = ("café" * 256).encode()
    add(
        "b64.decode_text",
        partial(b64.decode_text, base64.b64encode(utf8)),
        expect(utf8.decode()),
        "1280 UTF-8 bytes",
    )
    json_encoded = base64.b64encode(compact).decode()
    add(
        "b64.encode_json",
        partial(b64.encode_json, rows),
        expect(json_encoded),
        "32 records; compact JSON and standard base64",
    )
    add(
        "b64.decode_json",
        partial(b64.decode_json, json_encoded),
        expect(rows),
        "32 records; standard base64 and JSON decode",
    )

    def b64_file(fn: Callable[..., object], payload: bytes) -> bytes:
        sink = io.BytesIO()
        fn(io.BytesIO(payload), sink)
        return sink.getvalue()

    add(
        "b64.encode_file",
        partial(b64_file, b64.encode_file, raw),
        expect(base64.encodebytes(raw)),
        "4864 raw bytes; BytesIO creation and extraction included",
    )
    add(
        "b64.decode_file",
        partial(b64_file, b64.decode_file, base64.encodebytes(raw)),
        expect(raw),
        "wrapped base64; BytesIO creation and extraction included",
    )
    return out


def _pack_cases(*, comparison: bool = False) -> list[Case]:
    import numpy as np

    from unstd.pack import binpack

    fmt, values = "<4d", (1.0, 2.0, 3.0, 4.0)
    packed = struct.pack(fmt, *values)
    bulk = np.arange(1024, dtype="<f8")
    bulk_bytes = struct.pack("<1024d", *range(1024))

    def array_check(value: object) -> None:
        np.testing.assert_array_equal(value, bulk)

    def pack_into() -> bytes:
        sink = bytearray(32)
        binpack.pack_into(fmt, sink, 0, *values)
        return bytes(sink)

    specs: list[tuple[str, Callable[[], object], Callable[[object], None], str]] = [
        (
            "pack",
            lambda: binpack.pack(fmt, *values),
            expect(packed),
            "4 little-endian doubles",
        ),
        (
            "unpack",
            lambda: binpack.unpack(fmt, packed),
            expect(values),
            "4 little-endian doubles",
        ),
        (
            "unpack_from",
            lambda: binpack.unpack_from(fmt, packed, 0),
            expect(values),
            "4 little-endian doubles at offset zero",
        ),
        (
            "pack_into",
            pack_into,
            expect(packed),
            "4 doubles; bytearray allocation/extraction included",
        ),
        ("calcsize", partial(binpack.calcsize, fmt), expect(32), "4-double format"),
        (
            "iter_unpack",
            lambda: list(binpack.iter_unpack("<d", packed)),
            expect([(v,) for v in values]),
            "4 doubles; full iterator consumption included",
        ),
        (
            "Struct",
            partial(binpack.Struct, fmt),
            _instance(struct.Struct),
            "4-double compiled format constructor",
        ),
        (
            "pack_array",
            lambda: binpack.pack_array("<f8", bulk),
            expect(bulk_bytes),
            "1024 prebuilt NumPy doubles",
        ),
        (
            "pack_array#list",
            lambda: binpack.pack_array("<f8", list(range(1024))),
            expect(bulk_bytes),
            "1024 Python integers; list creation included",
        ),
        (
            "unpack_array",
            lambda: binpack.unpack_array("<f8", bulk_bytes),
            array_check,
            "1024 doubles; NumPy result",
        ),
        (
            "pack_array_portable",
            lambda: binpack.pack_array_portable("<f8", bulk),
            expect(bulk_bytes),
            "1024 prebuilt NumPy doubles; portable encoding",
        ),
        (
            "unpack_array_portable",
            lambda: binpack.unpack_array_portable("<f8", bulk_bytes),
            array_check,
            "1024 doubles; portable decoding",
        ),
    ]
    return [
        Case(f"unstd.pack.binpack.{name}", run, check, note)
        for name, run, check, note in specs
        if not comparison or hasattr(binpack, name.split("#", 1)[0])
    ]


def _ragged_cases(*, comparison: bool = False) -> list[Case]:
    """Check complete immutable numeric frames against fixed struct values."""
    import numpy as np

    if TYPE_CHECKING:
        from unstd.pack import ragged
    else:
        try:
            ragged = import_module("unstd.pack.ragged")
        except ModuleNotFoundError as error:
            if comparison and error.name == "unstd.pack.ragged":
                return []
            raise

    boundaries = tuple(range(0, 65, 2))
    numbers = tuple(float(value) for value in range(256))
    payload = struct.pack("<256f", *numbers)
    wire = struct.pack("<33I", *boundaries) + payload
    expected = tuple(struct.iter_unpack("<4f", payload))

    def decoder() -> ragged.RaggedDecoder:
        return ragged.RaggedDecoder(
            "<f4", columns=4, index_dtype="<u4", finite=True, allow_empty=False
        )

    compiled = decoder()

    def configured(value: Any) -> None:
        assert type(value) is ragged.RaggedDecoder
        assert (value.dtype, value.columns, value.index_dtype) == ("<f4", 4, "<u4")
        assert value.finite is True
        assert value.allow_empty is False

    def unpacked(value: Any) -> None:
        matrix, offsets = value
        np.testing.assert_array_equal(matrix, expected)
        np.testing.assert_array_equal(offsets, boundaries)
        assert matrix.dtype.str == "<f4"
        assert offsets.dtype.str == "<u4"
        assert not matrix.flags.writeable
        assert not offsets.flags.writeable

    return [
        Case(
            "unstd.pack.ragged.RaggedDecoder",
            decoder,
            configured,
            "compile four-column little-endian float32 rows and uint32 boundaries; finite, nonempty segments",
        ),
        Case(
            "unstd.pack.ragged.RaggedDecoder.unpack",
            partial(compiled.unpack, wire, index_count=33),
            unpacked,
            "validate 33 boundaries and 64 four-column finite float32 rows; immutable NumPy views",
        ),
    ]


def _compress_cases() -> list[Case]:
    from unstd.compress import gzipx

    raw = b"benchmark gzip record; alpha beta gamma\n" * 512
    packed = gzip.compress(raw, compresslevel=6, mtime=0)

    def compressed(value: object) -> None:
        assert isinstance(value, bytes)
        assert gzip.decompress(value) == raw

    def file_read() -> bytes:
        with gzipx.GzipFile(fileobj=io.BytesIO(packed), mode="rb") as source:
            return source.read()

    def file_open() -> bytes:
        with gzipx.open(io.BytesIO(packed), "rb") as source:
            return source.read()

    def file_write(*, flush: bool = False) -> bytes:
        target = io.BytesIO()
        with gzipx.GzipFile(
            fileobj=target, mode="wb", compresslevel=6, mtime=0
        ) as writer:
            writer.write(raw)
            if flush:
                writer.flush()
        return target.getvalue()

    return [
        Case(
            "unstd.compress.gzipx.compress",
            lambda: gzipx.compress(raw, compresslevel=6, mtime=0),
            compressed,
            "19456 bytes; gzip level 6, mtime zero",
        ),
        Case(
            "unstd.compress.gzipx.decompress",
            partial(gzipx.decompress, packed),
            expect(raw),
            "19456 decoded bytes; stdlib-produced gzip",
        ),
        Case(
            "unstd.compress.gzipx.GzipFile",
            file_read,
            expect(raw),
            "new BytesIO; full open/read/close lifecycle, 19456 decoded bytes",
        ),
        Case(
            "unstd.compress.gzipx.open",
            file_open,
            expect(raw),
            "new BytesIO; full open/read/close lifecycle, 19456 decoded bytes",
        ),
        Case(
            "unstd.compress.gzipx.GzipFile.write",
            file_write,
            compressed,
            "19456 bytes; BytesIO creation and full level-6 writer lifecycle included",
        ),
        Case(
            "unstd.compress.gzipx.GzipFile.flush",
            partial(file_write, flush=True),
            compressed,
            "19456 bytes; write/flush/close lifecycle, new BytesIO, level 6",
        ),
        Case(
            "unstd.compress.gzipx.GzipFile.close",
            file_write,
            compressed,
            "19456 bytes; full level-6 writer lifecycle and header finalization",
        ),
    ]


def _zlib_cases(*, comparison: bool = False) -> list[Case]:
    """Audit complete codec lifecycles against independent stdlib zlib decoding."""
    if TYPE_CHECKING:
        from unstd.compress import zlibx
    else:
        try:
            zlibx = import_module("unstd.compress.zlibx")
        except ModuleNotFoundError as exc:
            if comparison and exc.name == "unstd.compress.zlibx":
                return []
            raise

    raw = b"benchmark zlib record; alpha beta gamma\x00\xff\n" * 512
    dictionary = b"benchmark zlib record; alpha beta gamma\x00\xff\n"
    packed = zlib.compress(raw, level=6)
    midpoint = len(raw) // 2

    def compressed(wbits: int) -> Callable[[object], None]:
        def check(value: object) -> None:
            assert type(value) is bytes
            expect(raw)(zlib.decompress(value, wbits))

        return check

    def compress_buffer(wbits: int) -> bytes:
        return zlibx.compress(raw, level=6, wbits=wbits)

    def decompress_buffer(encoded: bytes, wbits: int) -> bytes:
        return zlibx.decompress(encoded, wbits=wbits)

    def compress_stream() -> bytes:
        encoder = zlibx.compressobj(level=6)
        return (
            encoder.compress(raw[:midpoint])
            + encoder.compress(raw[midpoint:])
            + encoder.flush()
        )

    def sync_flush() -> bytes:
        encoder = zlibx.compressobj(level=6)
        parts = []
        for offset in range(0, len(raw), 4096):
            parts.append(encoder.compress(raw[offset : offset + 4096]))
            parts.append(encoder.flush(zlib.Z_SYNC_FLUSH))
        parts.append(encoder.flush())
        return b"".join(parts)

    def compress_copy() -> tuple[bytes, bytes]:
        encoder = zlibx.compressobj(level=6)
        prefix = encoder.compress(raw) + encoder.flush(zlib.Z_SYNC_FLUSH)
        twin = encoder.copy()
        return (
            prefix + encoder.compress(b"left") + encoder.flush(),
            prefix + twin.compress(b"right") + twin.flush(),
        )

    def copied_compression(value: object) -> None:
        assert type(value) is tuple
        assert len(value) == 2
        for encoded, suffix in zip(value, (b"left", b"right"), strict=True):
            assert type(encoded) is bytes
            expect(raw + suffix)(zlib.decompress(encoded))

    def compress_dictionary() -> bytes:
        encoder = zlibx.compressobj(level=6, zdict=dictionary)
        return encoder.compress(raw) + encoder.flush()

    def dictionary_compression(value: object) -> None:
        assert type(value) is bytes
        assert len(value) >= 6
        assert value[1] & 0x20, "missing zlib dictionary flag"
        decoder = zlib.decompressobj(zdict=dictionary)
        expect(raw)(decoder.decompress(value) + decoder.flush())
        assert decoder.eof
        assert decoder.unused_data == b""

    def decompress_stream() -> bytes:
        decoder = zlibx.decompressobj()
        split = len(packed) // 2
        return (
            decoder.decompress(packed[:split])
            + decoder.decompress(packed[split:])
            + decoder.flush()
        )

    def drain(decoder: zlib._Decompress) -> tuple[bytes, bool, bytes, int]:
        parts = []
        largest = 0
        # Bound the harness even if a changed backend stops making progress.
        for _ in range(len(raw) + 1):
            if decoder.eof:
                break
            part = decoder.decompress(decoder.unconsumed_tail, max_length=257)
            largest = max(largest, len(part))
            parts.append(part)
            if not part and not decoder.unconsumed_tail:
                break
        return b"".join(parts), decoder.eof, decoder.unused_data, largest

    def bounded_decompression() -> tuple[bytes, bool, bytes, int]:
        decoder = zlibx.decompressobj()
        first = decoder.decompress(packed + b"following frame", max_length=17)
        rest, eof, unused, largest = drain(decoder)
        return first + rest, eof, unused, max(len(first), largest)

    def decompress_copy() -> tuple[bytes, bytes, bool, bytes, bytes]:
        decoder = zlibx.decompressobj()
        first = decoder.decompress(packed + b"following frame", max_length=17)
        twin = decoder.copy()
        left, left_eof, left_unused, _ = drain(decoder)
        independent = not twin.eof
        right, right_eof, right_unused, _ = drain(twin)
        return (
            first + left,
            first + right,
            left_eof and right_eof and independent,
            left_unused,
            right_unused,
        )

    prepared = zlib.compressobj(level=6, zdict=dictionary)
    packed_dictionary = prepared.compress(raw) + prepared.flush()

    def decompress_dictionary() -> bytes:
        decoder = zlibx.decompressobj(zdict=dictionary)
        return decoder.decompress(packed_dictionary) + decoder.flush()

    specs: list[tuple[str, Callable[[], object], Callable[[object], None], str]] = []
    for suffix, wbits in (("", 15), ("#raw", -15), ("#gzip", 31)):
        encoded = zlib.compress(raw, level=6, wbits=wbits)
        specs.extend(
            (
                (
                    "compress" + suffix,
                    partial(compress_buffer, wbits),
                    compressed(wbits),
                    f"{len(raw)} binary bytes; level 6, wbits {wbits}",
                ),
                (
                    "decompress" + suffix,
                    partial(decompress_buffer, encoded, wbits),
                    expect(raw),
                    f"{len(raw)} decoded bytes; stdlib-produced stream, wbits {wbits}",
                ),
            )
        )
    specs.extend(
        (
            (
                "compressobj",
                compress_stream,
                compressed(15),
                f"{len(raw)} bytes; new level-6 compressor, two inputs and finish",
            ),
            (
                "compressobj#sync-flush",
                sync_flush,
                compressed(15),
                f"{len(raw)} bytes; new compressor, 4096-byte inputs with sync flushes and finish",
            ),
            (
                "compressobj#copy",
                compress_copy,
                copied_compression,
                f"{len(raw)} shared prefix; native state copy and independent suffixes, both finished",
            ),
            (
                "compressobj#dictionary",
                compress_dictionary,
                dictionary_compression,
                f"{len(raw)} bytes; new compressor with preset dictionary and finish",
            ),
            (
                "decompressobj",
                decompress_stream,
                expect(raw),
                f"{len(raw)} bytes; new inflater, two compressed inputs and flush",
            ),
            (
                "decompressobj#bounded-tail",
                bounded_decompression,
                expect((raw, True, b"following frame", 257)),
                f"{len(raw)} bytes; new inflater, 17-byte first budget then 257-byte tail drains",
            ),
            (
                "decompressobj#copy",
                decompress_copy,
                expect((raw, raw, True, b"following frame", b"following frame")),
                f"{len(raw)} bytes; bounded prefix, copied inflater and independent 257-byte drains",
            ),
            (
                "decompressobj#dictionary",
                decompress_dictionary,
                expect(raw),
                f"{len(raw)} bytes; new dictionary inflater over stdlib-produced bytes and flush",
            ),
            (
                "crc32",
                lambda: zlibx.crc32(raw),
                expect(zlib.crc32(raw)),
                f"{len(raw)} binary bytes; default zero CRC state",
            ),
            (
                "crc32#signed-seed",
                lambda: zlibx.crc32(raw, -1),
                expect(zlib.crc32(raw, -1)),
                f"{len(raw)} binary bytes; signed seed masked to uint32",
            ),
            (
                "adler32",
                lambda: zlibx.adler32(raw),
                expect(zlib.adler32(raw)),
                f"{len(raw)} binary bytes; default Adler state one",
            ),
            (
                "adler32#signed-seed",
                lambda: zlibx.adler32(raw, -1),
                expect(zlib.adler32(raw, -1)),
                f"{len(raw)} binary bytes; signed seed masked to uint32",
            ),
        )
    )
    return [
        Case(f"unstd.compress.zlibx.{name}", run, check, note)
        for name, run, check, note in specs
        if not comparison or hasattr(zlibx, name.split("#", 1)[0])
    ]


def _audio_cases(*, comparison: bool = False) -> list[Case]:
    import numpy as np

    from unstd.audio import wavx

    samples = np.tile(np.array([0, 8192, -8192, 16384], dtype=np.int16), 64)
    raw = samples.astype("<i2").tobytes()
    sink = io.BytesIO()
    with wave.open(sink, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(raw)
    wav = sink.getvalue()

    def read_check(value: object) -> None:
        assert isinstance(value, tuple)
        assert len(value) == 2
        np.testing.assert_array_equal(value[0], samples)
        assert value[1] == 16000

    def block_check(value: object) -> None:
        assert isinstance(value, list)
        assert len(value) == 4
        np.testing.assert_array_equal(np.concatenate(value), samples)

    def pcm_check(value: object) -> None:
        assert isinstance(value, np.ndarray)
        assert value.dtype == np.float32
        assert value.flags.owndata
        assert value.flags.writeable
        np.testing.assert_array_equal(value, samples.astype(np.float32) / 32768.0)

    def wav_check(value: object) -> None:
        assert isinstance(value, bytes)
        with wave.open(io.BytesIO(value), "rb") as reader:
            assert reader.getnchannels() == 1
            assert reader.getsampwidth() == 2
            assert reader.getframerate() == 16000
            assert reader.readframes(257) == raw

    def write() -> bytes:
        target = io.BytesIO()
        wavx.write(target, samples, 16000)
        return target.getvalue()

    def stream() -> bytes:
        target = io.BytesIO()
        with wavx.Writer(target, 16000) as writer:
            writer.write(samples)
        return target.getvalue()

    def explicit_close() -> bytes:
        target = io.BytesIO()
        writer = wavx.Writer(target, 16000)
        try:
            writer.write(samples)
        finally:
            writer.close()
        return target.getvalue()

    out = [
        Case(
            "unstd.audio.wavx.read",
            lambda: wavx.read(io.BytesIO(wav), dtype="int16"),
            read_check,
            "256 mono PCM16 frames at 16 kHz; new BytesIO included",
        ),
        Case(
            "unstd.audio.wavx.blocks",
            lambda: list(wavx.blocks(io.BytesIO(wav), 64, dtype="int16")),
            block_check,
            "256 mono PCM16 frames in four 64-frame blocks; full consumption",
        ),
        Case(
            "unstd.audio.wavx.wrap_pcm",
            lambda: wavx.wrap_pcm(raw, 16000),
            expect(wav),
            "256 prebuilt PCM16 mono frames; no conversion",
        ),
        Case(
            "unstd.audio.wavx.write",
            write,
            wav_check,
            "256 mono PCM16 frames; new BytesIO and completed file included",
        ),
    ]
    if not comparison or hasattr(wavx, "decode_pcm"):
        out.append(
            Case(
                "unstd.audio.wavx.decode_pcm",
                partial(wavx.decode_pcm, raw),
                pcm_check,
                "256 mono raw PCM16 frames; owned writable float32 samples",
            )
        )
    for name, run in (
        ("Writer", stream),
        ("Writer.write", stream),
        ("Writer.close", explicit_close),
        ("Writer.__enter__", stream),
        ("Writer.__exit__", stream),
    ):
        out.append(
            Case(
                f"unstd.audio.wavx.{name}",
                run,
                wav_check,
                "256 mono PCM16 frames; complete bounded writer lifecycle included",
            )
        )
    return out


def _toml_cases() -> list[Case]:
    from unstd import toml

    text = 'name = "benchmark"\ncount = 32\nvalues = [1, 2, 3]\n'
    data = {"name": "benchmark", "count": 32, "values": [1, 2, 3]}

    def encoded(value: object) -> None:
        assert isinstance(value, str)
        expect(data)(toml.loads(value))

    def document(value: object) -> None:
        assert hasattr(value, "as_string")
        assert str(value.as_string()) == ""

    def empty(value: object) -> None:
        assert len(value) == 0  # type: ignore[arg-type]

    def item_check(value: object) -> None:
        assert hasattr(value, "unwrap")
        assert value.unwrap() == 32

    def comment_check(value: object) -> None:
        assert value.as_string() == "# benchmark\n"  # type: ignore[attr-defined]

    def newline_check(value: object) -> None:
        assert value.as_string() == "\n"  # type: ignore[attr-defined]

    def dump() -> str:
        target = io.StringIO()
        toml.dump(data, target)
        return target.getvalue()

    def parsed(value: object) -> None:
        assert value == data
        assert hasattr(value, "as_string")
        encoded(value.as_string())

    specs: list[tuple[str, Callable[[], object], Callable[[object], None], str]] = [
        (
            "loads",
            partial(toml.loads, text),
            expect(data),
            "three-field TOML 1.0 document",
        ),
        (
            "load",
            lambda: toml.load(io.BytesIO(text.encode())),
            expect(data),
            "three-field TOML document; new binary BytesIO",
        ),
        (
            "dumps",
            partial(toml.dumps, data),
            encoded,
            "three-field mapping; style-preserving encoder",
        ),
        (
            "dump",
            dump,
            encoded,
            "three-field mapping; new StringIO and extraction included",
        ),
        (
            "parse",
            partial(toml.parse, text),
            parsed,
            "three-field editable TOML document",
        ),
        ("document", toml.document, document, "empty editable document constructor"),
        ("table", toml.table, empty, "empty table constructor"),
        ("array", toml.array, empty, "empty array constructor"),
        ("inline_table", toml.inline_table, empty, "empty inline-table constructor"),
        ("aot", toml.aot, empty, "empty array-of-tables constructor"),
        ("item", partial(toml.item, 32), item_check, "integer TOML node constructor"),
        (
            "comment",
            partial(toml.comment, "benchmark"),
            comment_check,
            "single comment node",
        ),
        ("nl", toml.nl, newline_check, "newline node constructor"),
    ]
    return [
        Case(f"unstd.toml.{name}", run, check, note) for name, run, check, note in specs
    ]


def _yaml_cases() -> list[Case]:
    from unstd import yaml

    text = "name: benchmark\ncount: 32\nvalues: [1, 2, 3]\n"
    data = {"name": "benchmark", "count": 32, "values": [1, 2, 3]}
    many = "---\n" + text + "---\n" + text

    def encoded(value: object) -> None:
        assert isinstance(value, str)
        # JSON-compatible YAML here has a small independent structural oracle.
        import ruamel.yaml

        assert ruamel.yaml.YAML(typ="safe", pure=True).load(value) == data

    def encoded_many(value: object) -> None:
        assert isinstance(value, str)
        import ruamel.yaml

        assert list(ruamel.yaml.YAML(typ="safe", pure=True).load_all(value)) == [
            data,
            data,
        ]

    def parsed(value: object) -> None:
        assert isinstance(value, dict)
        assert dict(value) == data

    def dump() -> str:
        target = io.StringIO()
        yaml.dump(data, target)
        return target.getvalue()

    def dump_all() -> str:
        target = io.StringIO()
        yaml.dump_all([data, data], target)
        return target.getvalue()

    specs: list[tuple[str, Callable[[], object], Callable[[object], None], str]] = [
        (
            "loads",
            partial(yaml.loads, text),
            expect(data),
            "three-field safe YAML document",
        ),
        (
            "load",
            lambda: yaml.load(io.StringIO(text)),
            expect(data),
            "three-field YAML document; new StringIO",
        ),
        (
            "parse",
            partial(yaml.parse, text),
            parsed,
            "three-field editable YAML document",
        ),
        (
            "dumps",
            partial(yaml.dumps, data),
            encoded,
            "three-field mapping; Unicode YAML emission",
        ),
        (
            "dump",
            dump,
            encoded,
            "three-field mapping; new StringIO and extraction included",
        ),
        (
            "load_all",
            lambda: list(yaml.load_all(many)),
            expect([data, data]),
            "two three-field YAML documents; full iterator consumption",
        ),
        (
            "dump_all",
            dump_all,
            encoded_many,
            "two three-field YAML documents; new StringIO and extraction included",
        ),
    ]
    return [
        Case(f"unstd.yaml.{name}", run, check, note) for name, run, check, note in specs
    ]


def cases(stack: ExitStack, *, comparison: bool = False) -> list[Case]:
    """Build bounded, reusable cases; no handles survive individual calls."""
    del stack  # All fixtures are in-memory and each measured stream closes itself.
    return [
        *_serde_cases(comparison=comparison),
        *_pack_cases(comparison=comparison),
        *_ragged_cases(comparison=comparison),
        *_compress_cases(),
        *_zlib_cases(comparison=comparison),
        *_audio_cases(comparison=comparison),
        *_toml_cases(),
        *_yaml_cases(),
    ]
