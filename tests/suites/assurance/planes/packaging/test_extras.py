"""Each optional extra runs alone in a lock-pinned, offline installed consumer."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.packaging.artifacts import (
    CONSUMER_PROVENANCE,
    _quarantine_args,
    create_consumer,
    install_extra_closure,
)


if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.packaging.artifacts import Artifacts


with (REPO_ROOT / "pyproject.toml").open("rb") as _manifest:
    _EXTRAS = tuple(sorted(tomllib.load(_manifest)["project"]["optional-dependencies"]))

# These concrete modules have origins (unlike namespace packages such as google)
# and cover every direct optional distribution. Exact distribution comparison
# additionally checks each extra's transitive closure, without a shared dev env.
_BACKEND_MODULES = {
    "orjson": "orjson",
    "msgspec": "msgspec",
    "pybase64": "pybase64",
    "uuid-utils": "uuid_utils",
    "blake3": "blake3",
    "whenever": "whenever",
    "protobuf": "google.protobuf",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "irregex": "irgx",
    "sortedcontainers": "sortedcontainers",
    "immutables": "immutables",
    "numpy": "numpy",
    "rapidfuzz": "rapidfuzz",
    "soundfile": "soundfile",
    "tomlkit": "tomlkit",
    "ruamel-yaml": "ruamel.yaml",
    "zlib-ng": "zlib_ng",
}


def test_isolated_commands_preserve_the_frozen_resolver_policy(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nexclude-newer = "P3D"\n'
        "[tool.uv.exclude-newer-package]\n"
        'example = "P1D"\nengine = false\n',
        encoding="utf-8",
    )
    assert _quarantine_args(tmp_path) == [
        "--exclude-newer",
        "P3D",
        "--exclude-newer-package",
        "engine=false",
        "--exclude-newer-package",
        "example=P1D",
    ]


_EXTRA_CASES = {
    "serde": """
import base64, json, msgspec, orjson, pybase64
from unstd.serde import b64, jsonx, ndjson, structs
assert jsonx._encode is orjson.dumps
calls = []
real_encode = jsonx._encode
def observed_encode(*args, **kwargs):
    calls.append('orjson')
    return real_encode(*args, **kwargs)
jsonx._encode = observed_encode
assert jsonx.dumpb({'value': [1, 2]}) == b'{"value":[1,2]}'
assert calls == ['orjson']
assert jsonx.loads(b'{"value":[1,2]}') == {'value': [1, 2]}
assert b64._b64 is pybase64
real_b64 = pybase64.b64encode_as_string
def observed_b64(*args, **kwargs):
    calls.append('pybase64')
    return real_b64(*args, **kwargs)
pybase64.b64encode_as_string = observed_b64
assert b64.encode(b'wire') == base64.b64encode(b'wire').decode('ascii')
assert calls[-1] == 'pybase64'
class Row(msgspec.Struct):
    count: int
codec = structs.Codec(Row)
assert isinstance(codec.encode.__self__, msgspec.json.Encoder)
assert isinstance(codec.decode.__self__, msgspec.json.Decoder)
assert codec.encode(Row(7)) == b'{"count":7}'
assert codec.decode(b'{"count":7}') == Row(7)
assert codec.decode_or(b'{"count":"wrong"}') is None
assert ndjson.loads(ndjson.dumpb([Row(7)], codec=codec), codec=codec) == [Row(7)]
""",
    "ids": """
import uuid, uuid_utils
from unstd.ids import uid
assert uid._uuid7 is uuid_utils.uuid7
assert uuid.UUID(uid.new()).version == 7
assert uuid.UUID(hex=uid.new_hex()).variant == uuid.RFC_4122
assert uid.derive(uuid.NAMESPACE_DNS, 'python.org') == uuid.UUID('886313e1-3b8a-5372-9b90-0c9aee199e5d')
""",
    "crypto": """
import blake3
from unstd.crypto import digest, token
assert digest.blake3 is blake3.blake3
assert digest.hex(b'') == 'af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262'
native_absorb = digest._native_absorb
native_calls = []
if native_absorb is not None:
    from unstd.crypto import _stream_native
    assert native_absorb is _stream_native.absorb
    def observed_absorb(hasher, chunks):
        native_calls.append(type(chunks))
        return native_absorb(hasher, chunks)
    digest._native_absorb = observed_absorb
chunks = [bytes(range(251))] * 1024
stream = digest.Stream(chunks)
assert stream.raw() == blake3.blake3(b''.join(chunks)).digest()
assert stream.update(memoryview(b'suffix')).raw() == blake3.blake3(b''.join(chunks) + b'suffix').digest()
assert digest.Stream(iter(chunks)).hex() == digest.Stream(tuple(chunks)).hex()
if native_absorb is not None:
    assert native_calls == [list, tuple]
    digest._native_absorb = native_absorb
key = bytes(range(32))
claims = b'{"subject":"installed"}'
assert token.mac_raw(key, claims) == blake3.blake3(claims, key=key).digest()
minted = token.mint(key, claims)
assert token.verify(key, minted) == claims
assert token.verify(bytes(reversed(key)), minted) is None
""",
    "time": """
from datetime import UTC, datetime
import whenever
from unstd.time import zoned
assert zoned.ZonedDateTime is whenever.ZonedDateTime
earlier = zoned.wall(2023, 10, 29, 1, 15, 'Europe/London', disambiguate='earlier')
later = zoned.wall(2023, 10, 29, 1, 15, 'Europe/London', disambiguate='later')
assert isinstance(earlier, whenever.ZonedDateTime)
assert isinstance(later, whenever.ZonedDateTime)
assert zoned.to_utc(earlier) == datetime(2023, 10, 29, 0, 15, tzinfo=UTC)
assert zoned.to_utc(later) == datetime(2023, 10, 29, 1, 15, tzinfo=UTC)
""",
    "proto": """
from google.protobuf.timestamp_pb2 import Timestamp
from unstd.time import timeutil
timestamp = timeutil.proto_timestamp('1970-01-01T00:00:01.234Z')
assert isinstance(timestamp, Timestamp)
assert (timestamp.seconds, timestamp.nanos) == (1, 234000000)
assert timeutil.proto_timestamp_ms(-1).ToJsonString() == '1969-12-31T23:59:59.999Z'
""",
    "model": """
import os, pydantic, pydantic_settings
from unstd.model import FrozenModel, StrictModel, StrictSettings
assert issubclass(StrictModel, pydantic.BaseModel)
assert issubclass(StrictSettings, pydantic_settings.BaseSettings)
class Record(StrictModel):
    value: int
assert Record.model_validate_json('{"value":7}').model_dump() == {'value': 7}
try:
    Record.model_validate({'value': 7, 'untrusted': True})
except pydantic.ValidationError as error:
    assert error.errors()[0]['type'] == 'extra_forbidden'
else:
    raise AssertionError('unknown model key accepted')
class Point(FrozenModel):
    value: int
assert hash(Point(value=7)) == hash(Point(value=7))
class Settings(StrictSettings):
    model_config = pydantic_settings.SettingsConfigDict(env_prefix='UNSTD_INSTALLED_')
    port: int = 80
os.environ['UNSTD_INSTALLED_PORT'] = '8087'
assert Settings().port == 8087
""",
    "rex": """
import irgx
from unstd import rex
pattern = rex.compile('[a-z]+[0-9]+')
assert isinstance(pattern._native, irgx.Pattern)
match = pattern.fullmatch('item123')
assert isinstance(match, irgx.Match)
assert match.group() == 'item123' and match.span() == (0, 7)
assert pattern.fullmatch('item') is None
strict = rex.compile_linear('[a-z]+[0-9]+')
assert strict.fullmatch('item123').span() == (0, 7)
assert rex.compile_faithful('a$').search('a\\n').span() == (0, 1)
""",
    "containers": """
import immutables, sortedcontainers
from unstd import containers
assert containers.SortedList is sortedcontainers.SortedList
assert containers.SortedDict is sortedcontainers.SortedDict
assert containers.SortedSet is sortedcontainers.SortedSet
assert containers.Map is immutables.Map
values = containers.SortedList([3, 1]); values.add(2)
assert list(values) == [1, 2, 3]
assert list(containers.SortedDict({'b': 2, 'a': 1})) == ['a', 'b']
assert list(containers.SortedSet([2, 1, 2])) == [1, 2]
old = containers.Map({'a': 1}); new = old.set('a', 2)
assert old['a'] == 1 and new['a'] == 2
""",
    "rand": """
import numpy, random
from unstd import rand
from unstd.rand import draw
assert rand.HAVE_NUMPY is True
assert isinstance(draw._gen.bit_generator, numpy.random.PCG64)
rand.seed(2026); first = rand.floats(4)
rand.seed(2026); second = rand.floats(4)
assert isinstance(first, numpy.ndarray)
assert numpy.array_equal(first, second)
assert bool(numpy.all((first >= 0) & (first < 1)))
assert isinstance(rand.ints(4, 1, 2), numpy.ndarray)
assert rand.choices(['only'], 3) == ['only', 'only', 'only']
explicit = rand.Generator(173)
reference = random.Random(173)
assert explicit.backend == 'stdlib'
assert explicit.floats(3) == [reference.uniform(0.0, 1.0) for _ in range(3)]
assert isinstance(rand.Generator(173, backend='numpy').floats(3), numpy.ndarray)
""",
    "pack": """
import numpy, struct
from unstd.pack import binpack
assert binpack.np is numpy
wire = binpack.pack_array('>i2', [1, -2, 300])
assert wire == struct.pack('>3h', 1, -2, 300)
values = binpack.unpack_array('>i2', wire)
assert isinstance(values, numpy.ndarray)
assert values.tolist() == [1, -2, 300]
assert values.base is wire
assert binpack.unpack_array_portable('>i2', wire) == [1, -2, 300]
try:
    binpack.pack_array_portable('u1', [1.9])
except struct.error:
    pass
else:
    raise AssertionError('portable packing silently truncated a float')
""",
    "clone": """
from dataclasses import dataclass
from unstd import clone
@dataclass
class Record:
    values: list[int]
original = Record([1, 2]); copied = clone.deep(original)
copied.values.append(3)
assert copied == Record([1, 2, 3]) and original == Record([1, 2])
assert clone.asdict(original) == {'values': [1, 2]}
""",
    "text": """
import rapidfuzz
from unstd.text import fuzz
assert fuzz.ratio is rapidfuzz.distance.Indel.normalized_similarity
assert fuzz.distance is rapidfuzz.distance.Levenshtein.distance
assert fuzz.ratio('abcd', 'abdc') == 0.75
assert fuzz.distance('kitten', 'sitting') == 3
assert fuzz.extract('needle', ['other', 'needle'], limit=1) == [('needle', 1.0)]
assert fuzz.cdist(['same'], ['same', 'xxxx']) == [[1.0, 0.0]]
assert fuzz.indel_ratio('abcbc', 'bcabc') == 0.8
assert fuzz.difflib_ratio('abcbc', 'bcabc') == 0.6
assert fuzz.extract('abcbc', ['bcabc'], scorer=fuzz.difflib_ratio) == [('bcabc', 0.6)]
""",
    "audio": """
import io, numpy, soundfile, struct, wave
from unstd.audio import wavx
assert wavx._sf is soundfile
assert wavx._HAVE_SOUNDFILE is True
calls = []
real_write = soundfile.write
def observed_write(*args, **kwargs):
    calls.append('soundfile')
    return real_write(*args, **kwargs)
soundfile.write = observed_write
stream = io.BytesIO()
wavx.write(stream, numpy.array([0.0, 0.5, -1.0]), 8000)
assert calls == ['soundfile']
wire = stream.getvalue()
with wave.open(io.BytesIO(wire), 'rb') as reference:
    assert reference.readframes(3) == struct.pack('<3h', 0, 16384, -32768)
samples, rate = wavx.read(io.BytesIO(wire), dtype='int16')
assert isinstance(samples, numpy.ndarray)
assert rate == 8000 and samples.tolist() == [0, 16384, -32768]
""",
    "toml": """
import tomlkit
from unstd import toml
assert toml.dumps is tomlkit.dumps
assert toml.parse is tomlkit.parse
document = toml.parse('# retained\\nvalue = 1\\n')
document['value'] = 7
assert toml.dumps(document) == '# retained\\nvalue = 7\\n'
assert toml.loads(toml.dumps(document)) == {'value': 7}
""",
    "yaml": """
import ruamel.yaml
from unstd import yaml
assert yaml._YAML is ruamel.yaml.YAML
assert yaml.loads('enabled: true\\nvalue: 7\\n') == {'enabled': True, 'value': 7}
document = yaml.parse('# retained\\nvalue: 1\\n')
assert isinstance(document, ruamel.yaml.comments.CommentedMap)
document['value'] = 7
assert yaml.dumps(document) == '# retained\\nvalue: 7\\n'
try:
    yaml.loads('value: !unknown unsafe')
except yaml.YAMLError:
    pass
else:
    raise AssertionError('unsafe tag accepted')
""",
    "compress": """
import gzip, io
from zlib_ng import gzip_ng, zlib_ng
from unstd.compress import gzipx
assert gzipx._backend is gzip_ng
assert gzipx._native is zlib_ng
calls = []
real_compress = gzip_ng.compress
def observed_compress(*args, **kwargs):
    calls.append('zlib-ng')
    return real_compress(*args, **kwargs)
gzip_ng.compress = observed_compress
payload = b'installed native gzip payload' * 32
wire = gzipx.compress(payload, mtime=0)
assert calls == ['zlib-ng']
assert gzip.decompress(wire) == payload
assert gzipx.decompress(gzip.compress(payload, mtime=0)) == payload
stream = io.BytesIO()
with gzipx.GzipFile(fileobj=stream, mode='wb', mtime=0) as writer:
    writer.write(payload)
assert gzip.decompress(stream.getvalue()) == payload
""",
}


def test_every_declared_extra_has_an_isolated_backend_probe(
    artifacts: Artifacts,
) -> None:
    extras = artifacts.project["optional-dependencies"]
    assert isinstance(extras, dict)
    assert extras.keys() == _EXTRA_CASES.keys(), (
        f"add isolated runtime probes for extras {sorted(extras.keys() - _EXTRA_CASES.keys())}; "
        f"remove stale probes {sorted(_EXTRA_CASES.keys() - extras.keys())}"
    )
    direct_dependencies = {
        canonicalize_name(Requirement(requirement).name)
        for requirements in extras.values()
        for requirement in requirements
    }
    assert direct_dependencies == _BACKEND_MODULES.keys(), (
        "every optional distribution needs an explicit import/provenance probe"
    )


@pytest.mark.parametrize("extra", _EXTRAS)
def test_each_extra_executes_alone_with_exact_locked_dependency_closure(
    extra: str,
    pytestconfig: pytest.Config,
    artifacts: Artifacts,
    wheel: Path,
    tmp_path: Path,
) -> None:
    if not pytestconfig.getoption("--require-extras"):
        pytest.skip("isolated optional-backend consumers require --require-extras")
    consumer = create_consumer(tmp_path / f"{extra}-consumer", wheel)
    expected = install_extra_closure(artifacts, consumer, extra)
    expected["unstd"] = str(artifacts.project["version"])
    consumer.run(
        CONSUMER_PROVENANCE
        + f"""
import re
def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()
installed = {{normalized(dist.metadata['Name']): dist.version for dist in importlib.metadata.distributions()}}
assert installed == {expected!r}, installed
for distribution in importlib.metadata.distributions():
    assert Path(distribution.locate_file('')).resolve().is_relative_to(Path(sys.prefix).resolve())
for distribution, module in {_BACKEND_MODULES!r}.items():
    if distribution in installed:
        backend = importlib.import_module(module)
        assert Path(backend.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module
    else:
        try:
            specification = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            specification = None
        assert specification is None, (distribution, module)
"""
        + _EXTRA_CASES[extra]
        + """
for name, module in tuple(sys.modules.items()):
    if name.startswith('unstd.') and getattr(module, '__file__', None):
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), name
"""
    )
