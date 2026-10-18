"""Real, pristine base consumers exercise each stdlib surface and missing extra."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.packaging import CONSUMER_AUDIT, Consumer, clean_environment


if TYPE_CHECKING:
    from tests.support.packaging import Artifacts


_STDLIB_CASES = {
    "compress.gzipx": """
import gzip, io
from unstd.compress import gzipx
payload = b'installed gzip payload' * 8
assert gzipx.compress is gzip.compress
assert gzipx.GzipFile is gzip.GzipFile
assert gzip.decompress(gzipx.compress(payload, mtime=0)) == payload
assert gzipx.decompress(gzip.compress(payload, mtime=0)) == payload
stream = io.BytesIO()
with gzipx.GzipFile(fileobj=stream, mode='wb', mtime=0) as writer:
    writer.write(payload)
assert gzip.decompress(stream.getvalue()) == payload
""",
    "serde.jsonx": """
import io, json
from unstd.serde import jsonx
value = {'unicode': '雪', 'nested': [None, True, -7, 1.5]}
assert jsonx.dumps(value) == json.dumps(value)
assert jsonx.loads(jsonx.dumpb(value)) == value
assert jsonx.canonical({'b': 2, 'a': 1}) == b'{"a":1,"b":2}'
stream = io.StringIO(); jsonx.dump(value, stream)
assert jsonx.load(io.StringIO(stream.getvalue())) == value
""",
    "serde.b64": """
import base64
from unstd.serde import b64
raw = b'\\x00\\xffpayload'
assert b64.encode(raw) == base64.b64encode(raw).decode('ascii')
assert b64.decode(b64.encode(raw, url=True, pad=False), url=True) == raw
assert b64.decode_json(b64.encode_json({'n': 3})) == {'n': 3}
""",
    "serde.ndjson": """
import io, json
from unstd.serde import ndjson
rows = [{'id': 1}, {'id': 2}]
wire = ndjson.dumpb(rows)
assert [json.loads(line) for line in wire.splitlines()] == rows
assert ndjson.loads(wire) == rows
assert list(ndjson.load(io.BytesIO(wire))) == rows
""",
    "crypto.tls": """
import ssl
from unstd.crypto import tls
context = tls.client_context()
assert isinstance(context, ssl.SSLContext)
assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
assert context.minimum_version == ssl.TLSVersion.TLSv1_3
""",
    "fs": """
from pathlib import Path
from unstd import fs
directory = fs.ensure_dir(Path('files') / 'nested')
destination = directory / 'example.txt'
assert fs.atomic_write(destination, 'old').read_text() == 'old'
with fs.atomic_writer(destination) as stream:
    stream.write('complete new')
assert destination.read_text() == 'complete new'
try:
    with fs.atomic_writer(destination) as stream:
        stream.write('torn'); raise RuntimeError('injected')
except RuntimeError:
    pass
assert destination.read_text() == 'complete new'
assert list(fs.walk_files('files')) == [destination]
replacement = directory / 'replacement'; replacement.write_bytes(b'replaced')
assert fs.atomic_replace(replacement, destination).read_bytes() == b'replaced'
""",
    "proc": """
import sys
from unstd import proc
assert proc.capture([sys.executable, '-I', '-c', 'print("installed")'], timeout=5) == 'installed'
result = proc.run([sys.executable, '-I', '-c', 'import sys; print("out"); print("err", file=sys.stderr); sys.exit(7)'], check=False, timeout=5)
assert result.returncode == 7 and result.stdout == 'out\\n' and result.stderr == 'err\\n'
assert proc.run_ok([sys.executable, '-I', '-c', 'pass'], timeout=5)
""",
    "clone": """
from dataclasses import dataclass
from unstd import clone
@dataclass
class Record:
    values: list[int]
original = Record([1, 2]); copied = clone.deep(original)
assert type(copied) is Record and copied == original
copied.values.append(3)
assert original.values == [1, 2]
assert clone.asdict(original) == {'values': [1, 2]}
assert clone.deepcopy({'a': [1]}) == {'a': [1]}
""",
    "ids.uid": """
import uuid
from unstd.ids import uid
assert uuid.UUID(uid.new()).version == 7
assert uuid.UUID(hex=uid.new_hex()).variant == uuid.RFC_4122
assert uid.derive(uuid.NAMESPACE_DNS, 'python.org') == uuid.UUID('886313e1-3b8a-5372-9b90-0c9aee199e5d')
assert uid.parse('886313e1-3b8a-5372-9b90-0c9aee199e5d').version == 5
""",
    "iters": """
from unstd import iters
assert list(iters.chunked(range(5), 2)) == [(0, 1), (2, 3), (4,)]
assert list(iters.windowed(range(4), 2)) == [(0, 1), (1, 2), (2, 3)]
assert list(iters.unique_everseen([1, 2, 1])) == [1, 2]
assert list(iters.flatten([[1], [2, 3]])) == [1, 2, 3]
assert iters.take(iter(range(4)), 2) == [0, 1]
assert iters.first([4]) == iters.last([4]) == iters.nth([4], 0) == 4
assert iters.ilen(iter([1, 2, 3])) == 3
""",
    "pack.binpack": """
import struct
from unstd.pack import binpack
expected = b'\\x00\\x00\\x80?\\x00\\x00 \\xc0'
assert binpack.pack_array('<f4', [1.0, -2.5]) == expected
assert binpack.unpack_array('<f4', expected) == [1.0, -2.5]
assert binpack.pack('>H', 0x1234) == b'\\x12\\x34'
assert binpack.unpack('>H', b'\\x12\\x34') == (0x1234,)
assert binpack.Struct is struct.Struct
""",
    "rand": """
import random
from unstd import rand
assert rand.HAVE_NUMPY is False
rand.seed(2026)
expected = random.Random(2026)
assert rand.random() == expected.random()
assert rand.floats(3) == [expected.uniform(0.0, 1.0) for _ in range(3)]
assert rand.ints(3, 4, 5) == [4, 4, 4]
assert rand.choices(['only'], 2) == ['only', 'only']
assert len(rand.crypto.token_bytes(16)) == 16
assert len(rand.crypto.token_hex(8)) == 16
""",
    "rex": """
import re
from unstd import rex
assert rex.findall(r'([a-z]+)(\\d+)', 'item12 item34') == [('item', '12'), ('item', '34')]
assert rex.sub(r'\\d+', '#', 'item12 item34') == 'item# item#'
assert rex.compile(r'(?P<word>\\w+)').fullmatch('雪').groupdict() == {'word': '雪'}
assert rex.error is re.error
""",
    "text.fuzz": """
import difflib
from unstd.text import fuzz
assert fuzz.ratio('abcd', 'abdc') == difflib.SequenceMatcher(None, 'abcd', 'abdc').ratio()
assert fuzz.distance('kitten', 'sitting') == 3
assert fuzz.jaro_winkler('same', 'same') == 1.0
assert fuzz.best_match('needle', ['other', 'needle']) == ('needle', 1.0)
""",
    "time.timeutil": """
from datetime import UTC, datetime
from unstd.time import timeutil
instant = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
assert timeutil.iso(instant) == '2026-01-02T03:04:05Z'
assert timeutil.parse_utc('2026-01-02T03:04:05Z') == instant
assert timeutil.from_epoch_ms(0) == datetime(1970, 1, 1, tzinfo=UTC)
assert timeutil.span('2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', unit='seconds') == 60.0
""",
    "toml": """
import io, tomllib
from unstd import toml
text = 'title = "installed"\\n[nested]\\ncount = 3\\n'
assert toml.loads(text) == {'title': 'installed', 'nested': {'count': 3}}
assert toml.load(io.BytesIO(text.encode())) == tomllib.loads(text)
""",
    "audio.wavx": """
import io, struct, wave
from unstd.audio import wavx
pcm = struct.pack('<4h', -32768, 0, 16384, 32767)
wire = wavx.wrap_pcm(pcm, 8000, channels=1, width=2)
with wave.open(io.BytesIO(wire), 'rb') as reference:
    assert reference.getnframes() == 4 and reference.getframerate() == 8000
    assert reference.readframes(4) == pcm
samples, rate = wavx.read(io.BytesIO(wire), dtype='int16')
assert rate == 8000 and samples == [-32768, 0, 16384, 32767]
output = io.BytesIO(); wavx.write(output, [0.0, 0.5, -1.0], 8000)
with wave.open(io.BytesIO(output.getvalue()), 'rb') as reference:
    assert reference.readframes(3) == struct.pack('<3h', 0, 16384, -32768)
""",
}


_MISSING_EXTRA_CASES = {
    "crypto.digest": ("crypto", "importlib.import_module('unstd.crypto.digest')"),
    "crypto.token": ("crypto", "importlib.import_module('unstd.crypto.token')"),
    "serde.structs": ("serde", "importlib.import_module('unstd.serde.structs')"),
    "model": ("model", "importlib.import_module('unstd.model')"),
    "time.zoned": ("time", "importlib.import_module('unstd.time.zoned')"),
    "containers.SortedList": (
        "containers",
        "importlib.import_module('unstd.containers').SortedList()",
    ),
    "containers.SortedDict": (
        "containers",
        "importlib.import_module('unstd.containers').SortedDict()",
    ),
    "containers.SortedSet": (
        "containers",
        "importlib.import_module('unstd.containers').SortedSet()",
    ),
    "containers.Map": (
        "containers",
        "importlib.import_module('unstd.containers').Map()",
    ),
    "toml.dumps": ("toml", "importlib.import_module('unstd.toml').dumps({'x': 1})"),
    "toml.parse": ("toml", "importlib.import_module('unstd.toml').parse('x = 1')"),
    "yaml.loads": ("yaml", "importlib.import_module('unstd.yaml').loads('x: 1')"),
    "yaml.dumps": ("yaml", "importlib.import_module('unstd.yaml').dumps({'x': 1})"),
    "yaml.load_all": (
        "yaml",
        "next(importlib.import_module('unstd.yaml').load_all('x: 1'))",
    ),
    "time.proto_timestamp": (
        "proto",
        "importlib.import_module('unstd.time.timeutil').proto_timestamp('2026-01-01T00:00:00Z')",
    ),
    "time.proto_timestamp_ms": (
        "proto",
        "importlib.import_module('unstd.time.timeutil').proto_timestamp_ms(0)",
    ),
}


# Explicitly assign every public import surface to real consumer behavior. This
# is intentionally not generated from the package: a new module must make an
# author choose and implement its installed/fallback/missing-extra contract.
_PROBE_MODULES = {
    "compress.gzipx": ("unstd.compress", "unstd.compress.gzipx"),
    "serde.jsonx": ("unstd.serde", "unstd.serde.jsonx"),
    "serde.b64": ("unstd.serde.b64",),
    "serde.ndjson": ("unstd.serde.ndjson",),
    "serde.structs": ("unstd.serde.structs",),
    "crypto.tls": ("unstd.crypto", "unstd.crypto.tls"),
    "crypto.digest": ("unstd.crypto.digest",),
    "crypto.token": ("unstd.crypto.token",),
    "fs": ("unstd.fs",),
    "proc": ("unstd.proc",),
    "clone": ("unstd.clone", "unstd.clone.structural"),
    "ids.uid": ("unstd.ids", "unstd.ids.uid"),
    "iters": (
        "unstd.iters",
        "unstd.iters.consecutive",
        "unstd.iters.distinct",
        "unstd.iters.reshape",
        "unstd.iters.select",
    ),
    "pack.binpack": ("unstd.pack", "unstd.pack.binpack"),
    "rand": ("unstd.rand", "unstd.rand.crypto", "unstd.rand.draw"),
    "rex": ("unstd.rex",),
    "text.fuzz": ("unstd.text", "unstd.text.fuzz"),
    "time.timeutil": ("unstd.time", "unstd.time.timeutil"),
    "time.zoned": ("unstd.time.zoned",),
    "toml": ("unstd.toml",),
    "audio.wavx": ("unstd.audio", "unstd.audio.wavx"),
    "containers.SortedList": ("unstd.containers", "unstd.containers.sorted"),
    "containers.Map": ("unstd.containers.persistent",),
    "model": ("unstd.model", "unstd.model.base", "unstd.model.settings"),
    "yaml.loads": ("unstd.yaml",),
}


def test_every_public_module_has_an_installed_behavior_probe(
    artifacts: Artifacts,
) -> None:
    modules = set()
    for name in artifacts.package_files:
        parts = name.removesuffix(".py").split("/")
        if not name.endswith(".py") or len(parts) < 3:
            continue
        if parts[-1] == "__init__":
            parts.pop()
        if not any(part.startswith("_") for part in parts):
            modules.add(".".join(parts))
    covered = {module for targets in _PROBE_MODULES.values() for module in targets}
    assert _PROBE_MODULES.keys() <= _STDLIB_CASES.keys() | _MISSING_EXTRA_CASES.keys()
    assert modules == covered, (
        f"installed consumer probes must cover every public module; "
        f"add a real behavior probe for {sorted(modules - covered)}, "
        f"remove stale probes for {sorted(covered - modules)}"
    )


def test_pristine_environment_provenance_and_installed_version(
    consumer: Consumer, artifacts: Artifacts
) -> None:
    result = consumer.run(
        CONSUMER_AUDIT
        + "\nimport json\nprint(json.dumps({'version': unstd.__version__, 'metadata': importlib.metadata.version('unstd'), 'origin': unstd.__file__, 'path': sys.path}))"
    )
    facts = json.loads(result.stdout)
    assert facts["version"] == facts["metadata"] == artifacts.project["version"]


@pytest.mark.parametrize("surface", _STDLIB_CASES)
def test_base_install_executes_each_stdlib_surface(
    surface: str, consumer: Consumer
) -> None:
    consumer.run(
        CONSUMER_AUDIT
        + _STDLIB_CASES[surface]
        + f"""
for module in {_PROBE_MODULES[surface]!r}:
    assert module in sys.modules, module
    assert Path(sys.modules[module].__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module
"""
    )


@pytest.mark.parametrize("surface", _MISSING_EXTRA_CASES)
def test_base_install_refuses_unavailable_capabilities_with_actionable_extra(
    surface: str, consumer: Consumer
) -> None:
    extra, operation = _MISSING_EXTRA_CASES[surface]
    consumer.run(
        CONSUMER_AUDIT
        + f"""
try:
    {operation}
except ImportError as error:
    assert 'unstd[{extra}]' in str(error), str(error)
    assert 'pip install' in str(error), str(error)
else:
    raise AssertionError('unavailable capability unexpectedly succeeded: {surface}')
"""
    )


def test_ambient_python_and_uv_policy_cannot_enter_the_consumer_environment(
    monkeypatch: pytest.MonkeyPatch, consumer: Consumer
) -> None:
    for variable in (
        "PYTHONPATH",
        "PYTHONHOME",
        "MYPYPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_SYSTEM_PYTHON",
        "UV_INDEX_URL",
        "UV_NO_BUILD_ISOLATION",
        "UV_NO_VERIFY_HASHES",
        "UV_FIND_LINKS",
    ):
        monkeypatch.setenv(variable, "/deliberately/invalid/packaging-test")
    environment = clean_environment()
    assert not any(
        value == "/deliberately/invalid/packaging-test"
        for value in environment.values()
    )
    consumer.run(CONSUMER_AUDIT)
