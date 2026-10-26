"""Complete-buffer C encoding preserves gzip and fallback operation contracts."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import random
import struct
import subprocess
import sys
import textwrap
from threading import Barrier
from typing import TYPE_CHECKING
import zlib

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture(scope="module")
def native_encoder() -> ModuleType:
    require_backend("zlib_ng.zlib_ng")
    native = require_backend("unstd.compress._gzip_native")
    assert callable(getattr(native, "compress", None)), "rebuild the native encoder"
    return native


@pytest.fixture(scope="module")
def request_bytes() -> dict[str, bytes]:
    path = REPO_ROOT / "bench/workloads/billy_gzip_requests.json.gz"
    document = json.loads(gzip.decompress(path.read_bytes()))
    chunks = {
        digest: base64.b64decode(encoded, validate=True)
        for digest, encoded in document["chunks"].items()
    }
    bodies = {
        digest: b"".join(chunks[piece] for piece in pieces)
        for digest, pieces in document["bodies"].items()
    }
    for digest, value in bodies.items():
        assert hashlib.sha256(value).hexdigest() == digest
    return {
        case["name"]: bodies[case["requests"][0]["sha256"]]
        for case in document["cases"]
        if case["mode"] == "direct"
    }


@pytest.mark.parametrize("size", [4, 128, 512])
@pytest.mark.parametrize("level", [6, 9])
def test_real_request_encoder_route_roundtrip_headers_and_quality(
    native_encoder, request_bytes, size, level
):
    from unstd.compress import gzipx

    raw = request_bytes[f"request-direct-{size}k-level6"]
    packed = native_encoder.compress(raw, level)
    assert type(packed) is bytes
    assert gzipx.compress(raw, level, mtime=0) == packed
    assert gzipx.compress(raw, level, mtime=0) == packed
    assert packed[:8] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00"
    assert packed[8:10] == bytes([2 if level == 9 else 0, 255])
    assert struct.unpack("<II", packed[-8:]) == (zlib.crc32(raw), len(raw))
    decoder = zlib.decompressobj(31)
    assert decoder.decompress(packed) + decoder.flush() == raw
    assert decoder.eof
    assert not decoder.unused_data
    assert not decoder.unconsumed_tail
    previous = gzipx._backend.compress(raw, level, mtime=0)
    assert len(packed) <= len(previous) * 1.01, "frozen request quality regressed"


@pytest.mark.parametrize(
    ("kind", "level"),
    [
        ("run", 6),
        ("run", 9),
        ("period", 6),
        ("period", 9),
        ("words", 6),
        ("words", 9),
        ("pretty", 6),
        ("unicode", 6),
        ("late-unicode", 6),
        ("csv", 6),
        ("empty", 9),
    ],
)
def test_unsupported_workload_classes_retain_previous_encoder(
    native_encoder, kind, level
):
    from unstd.compress import gzipx

    rng = random.Random(3117)  # noqa: S311 — reproducible public compression corpus
    payloads = {
        "run": b"a" * 131072,
        "period": bytes(range(32)) * 4096,
        "words": json.dumps(
            [rng.choice(["cedar", "copper", "river", "mountain"]) for _ in range(4096)]
        ).encode(),
        "pretty": json.dumps(
            [{"name": f"public-{i}", "value": i} for i in range(1000)], indent=2
        ).encode(),
        "unicode": json.dumps(
            [{"name": f"café-{i}", "value": i} for i in range(1000)], ensure_ascii=False
        ).encode(),
        "late-unicode": json.dumps(
            {
                "header": [{"name": f"public-{i}", "value": i} for i in range(200)],
                "tail": "café",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
        "csv": b"id,name,value\n" + b"1,public,42\n" * 1000,
        "empty": b"",
    }
    raw = payloads[kind]
    if kind == "late-unicode":
        assert (
            native_encoder.compress(raw.replace("café".encode(), b"cafe"), 6)
            is not None
        )
    assert native_encoder.compress(raw, level) is None
    assert gzipx.compress(raw, level, mtime=0) == gzipx._backend.compress(
        raw, level, mtime=0
    )


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4, 5, 7, 8])
def test_other_levels_retain_the_native_provider(native_encoder, request_bytes, level):
    from unstd.compress import gzipx

    raw = request_bytes["request-direct-4k-level6"]
    assert native_encoder.compress(raw, level) is None
    assert gzipx.compress(raw, level, mtime=0) == gzipx._backend.compress(
        raw, level, mtime=0
    )


@pytest.mark.parametrize("mtime", [123, 0.0, False])
def test_other_timestamp_values_retain_native_provider(
    native_encoder, request_bytes, mtime
):
    from unstd.compress import gzipx

    raw = request_bytes["request-direct-4k-level6"]
    assert gzipx.compress(raw, 9, mtime=mtime) == gzipx._backend.compress(
        raw, 9, mtime=mtime
    )


@pytest.mark.parametrize("error", [MemoryError, RuntimeError])
def test_encoder_failure_is_not_replayed(
    native_encoder, request_bytes, monkeypatch, error
):
    from unstd.compress import gzipx

    failure = error("native compression failure")

    def failed(*_args):
        raise failure

    def replayed(*_args, **_kwargs):
        pytest.fail("compression was replayed after a native failure")

    monkeypatch.setattr(gzipx, "_encode_buffer", failed)
    monkeypatch.setattr(gzipx._backend, "compress", replayed)
    with pytest.raises(error) as caught:
        gzipx.compress(request_bytes["request-direct-4k-level6"], 9, mtime=0)
    assert caught.value is failure


def test_concurrent_encoders_own_their_native_state(native_encoder, request_bytes):
    barrier = Barrier(4)
    base = request_bytes["request-direct-128k-level6"]

    def encode(worker):
        raw = base.replace(b"public_", f"public_{worker}".encode(), 1)
        level = 6 if worker % 2 else 9
        barrier.wait()
        for _ in range(8):
            packed = native_encoder.compress(raw, level)
            assert packed is not None
            assert gzip.decompress(packed) == raw

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(encode, range(4)))


def _isolated(source):
    result = subprocess.run(  # noqa: S603 — fixed native contract probe, this interpreter
        [sys.executable, "-c", textwrap.dedent(source)],
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_large_encoder_releases_the_gil(native_encoder):
    _isolated("""\
        import gzip, sys, threading
        from random import Random
        from unstd.compress import _gzip_native as native
        raw = Random(173).randbytes(8 * 1024 * 1024).translate(bytes(i & 127 for i in range(256)))
        ready, waiting = threading.Event(), threading.Event()
        observed = []
        finished = False
        def observe():
            waiting.set()
            ready.wait()
            observed.append(not finished)
        thread = threading.Thread(target=observe)
        thread.start()
        assert waiting.wait(5)
        old = sys.getswitchinterval()
        sys.setswitchinterval(30)
        try:
            ready.set()
            packed = native.compress(raw, 9)
            finished = True
        finally:
            sys.setswitchinterval(old)
        thread.join(5)
        assert not thread.is_alive() and observed == [True]
        assert packed is not None and gzip.decompress(packed) == raw
    """)


@pytest.mark.parametrize("mode", ["old-native", "missing-native", "base"])
def test_encoder_capability_absence_retains_existing_routes(native_encoder, mode):
    _isolated(
        f"MODE = {mode!r}\n"
        + """\
import builtins, gzip, importlib.abc, sys
if MODE == 'old-native':
    original = builtins.__import__
    def load(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'unstd.compress._gzip_native' and 'compress' in fromlist:
            raise ImportError('older native encoder surface')
        return original(name, globals, locals, fromlist, level)
    builtins.__import__ = load
else:
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if ((MODE == 'base' and (name == 'zlib_ng' or name.startswith('zlib_ng.')))
                    or (MODE == 'missing-native' and name == 'unstd.compress._gzip_native')):
                raise ImportError('controlled capability absence')
    sys.meta_path.insert(0, Block())
from unstd.compress import gzipx
if MODE == 'base':
    assert gzipx.compress is gzip.compress
else:
    assert gzipx._encode_buffer is None
    if MODE == 'old-native':
        from unstd.compress import _gzip_native as native
        assert gzipx._decode_buffer is native.decompress
    else:
        assert gzipx._decode_buffer is None
raw = b'capability fallback' * 500
assert gzip.decompress(gzipx.compress(raw, mtime=0)) == raw
"""
    )


_FALLBACK_CASES = (
    "plain6",
    "plain9",
    "bytes-subclass",
    "bytearray",
    "view",
    "wide-view",
    "strided-view",
    "exporter",
    "bad-exporter",
    "string-data",
    "none-data",
    "level-index",
    "level-subclass",
    "level-bool",
    "mtime-clock",
    "mtime-index",
    "mtime-subclass",
    "mtime-float",
    "mtime-bool",
    "mtime-none",
    "mtime-negative",
    "mtime-overflow",
    *[
        f"level-{value}"
        for value in (-2, -1, 0, 1, 2, 3, 4, 5, 7, 8, 10, 1 << 99, None, 1.5, "9")
    ],
)


def _trace_compression(module, name):
    events = []
    raw = json.dumps(
        [{"id": i, "value": f"public-{i}"} for i in range(200)], separators=(",", ":")
    ).encode()

    class Index:
        def __init__(self, value, label):
            self.value, self.label = value, label

        def __index__(self):
            events.append(self.label)
            return self.value

    class Clock:
        def __int__(self):
            events.append("int-mtime")
            return 0

    class Integer(int):
        def __index__(self):
            events.append("subclass-index")
            return int(self)

    class ByteSubclass(bytes):
        def __len__(self):
            events.append("len-subclass")
            return bytes.__len__(self)

        def __getitem__(self, index):
            events.append("getitem-subclass")
            return bytes.__getitem__(self, index)

    class Exporter:
        def __buffer__(self, flags):
            events.append(f"buffer:{flags}")
            return memoryview(raw)

        def __release_buffer__(self, view):
            events.append("release")

    class BadExporter:
        def __buffer__(self, flags):
            events.append(f"badbuffer:{flags}")
            return b"wrong buffer result"

    data, level, mtime = raw, 9, 0
    choices = {
        "plain6": (raw, 6, 0),
        "plain9": (raw, 9, 0),
        "bytes-subclass": (ByteSubclass(raw), 9, 0),
        "bytearray": (bytearray(raw), 9, 0),
        "view": (memoryview(raw), 9, 0),
        "wide-view": (memoryview(b"abcd" * 500).cast("H"), 9, 0),
        "strided-view": (memoryview(raw)[::2], 9, 0),
        "exporter": (Exporter(), 9, 0),
        "bad-exporter": (BadExporter(), 9, 0),
        "string-data": ("text", 9, 0),
        "none-data": (None, 9, 0),
        "level-index": (raw, Index(6, "index-level"), 0),
        "level-subclass": (raw, Integer(9), 0),
        "level-bool": (raw, True, 0),
        "mtime-clock": (raw, 9, Clock()),
        "mtime-index": (raw, 9, Index(0, "index-mtime")),
        "mtime-subclass": (raw, 9, Integer(0)),
        "mtime-float": (raw, 9, 0.0),
        "mtime-bool": (raw, 9, False),
        "mtime-none": (raw, 9, None),
        "mtime-negative": (raw, 9, -1),
        "mtime-overflow": (raw, 9, 1 << 40),
    }
    choices.update(
        {
            f"level-{value}": (raw, value, 0)
            for value in (-2, -1, 0, 1, 2, 3, 4, 5, 7, 8, 10, 1 << 99, None, 1.5, "9")
        }
    )
    data, level, mtime = choices[name]
    try:
        packed = module.compress(data, level, mtime=mtime)
    except BaseException as error:
        return events, (type(error), error.args)
    decoded = gzip.decompress(packed)
    metadata = (decoded, "current" if mtime is None else packed[4:8], packed[8:10])
    if name not in {"plain6", "plain9", "mtime-none"}:
        metadata += (packed,)
    return events, metadata


@pytest.mark.parametrize("case", _FALLBACK_CASES)
def test_argument_callbacks_and_errors_match_real_provider_fallback(
    native_encoder, monkeypatch, case
):
    from unstd.compress import gzipx

    encoder = gzipx._encode_buffer
    assert encoder is not None
    monkeypatch.setattr(gzipx, "_encode_buffer", None)
    before = _trace_compression(gzipx, case)
    monkeypatch.setattr(gzipx, "_encode_buffer", encoder)
    assert _trace_compression(gzipx, case) == before
