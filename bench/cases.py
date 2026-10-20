"""What gets compared against what, and on which payload.

Every case is the same shape: an accelerated ``unstd`` call and the stdlib call
it stands in for, over one payload, so the reported number is a **ratio** rather
than an absolute. That is deliberate — absolute microseconds are a fact about
whichever laptop ran them and rot the moment they are written down, while
"orjson encodes this payload 4× faster than ``json`` does" holds across machines
within a wide band. The ratio is what the baseline pins.

A case declares the import that must succeed for its accelerated path to be real
(``backend``). Without it ``unstd`` has silently fallen back to the stdlib, both
sides of the comparison are the *same function*, and the 1.0× that comes out
means nothing. Those cases are reported as skipped rather than measured, because
a harness that grades a missing backend as "no regression" is worse than none.

Every callable is bound at import time. A ``__import__`` or attribute walk inside
the measured lambda would add a few hundred nanoseconds to each iteration, which
is invisible on the 1 MiB hash case and roughly the entire measurement on the
small-dict clone.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
from dataclasses import dataclass
import datetime
import difflib
from functools import cache
import gzip
import hashlib
import importlib.util
import io
import json
import random
import re
import struct
from typing import TYPE_CHECKING, Any

from unstd.clone import asdict as clone_asdict
from unstd.clone import deep
from unstd.compress import gzipx
from unstd.crypto.digest import raw as digest_raw
from unstd.pack.binpack import pack_array, unpack_array
from unstd.rand.draw import floats, ints
from unstd.rex import compile as rex_compile
from unstd.rex import findall as rex_findall
from unstd.rex import search as rex_search
from unstd.serde import ndjson
from unstd.serde.b64 import decode as b64_decode
from unstd.serde.b64 import encode as b64_encode
from unstd.serde.jsonx import dumpb as jx_dumpb
from unstd.serde.jsonx import dumps as jx_dumps
from unstd.serde.jsonx import loads as jx_loads
from unstd.text.fuzz import _levenshtein, _sequence_distance, cdist, extract, ratio


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from unstd.serde.jsonx import Json
    from unstd.serde.structs import Codec


@dataclass(frozen=True, slots=True)
class Case:
    """One accelerated call measured against its stdlib twin."""

    name: str
    group: str
    backend: str
    """The accelerated backend, named for the reader and probed for by default."""
    payload: Callable[[], Any]
    accel: Callable[[Any], Any]
    base: Callable[[Any], Any]
    note: str = ""
    equivalent: bool = True
    """Whether both sides compute the same answer. ``False`` marks a case where
    the accelerated path is a *different, better* algorithm rather than a faster
    spelling of the same one — the ratio is then a capability claim, not a
    like-for-like speedup, and the note has to say which."""
    probe: Callable[[], bool] | None = None
    """Overrides the import check when importability does not prove the fast path
    was taken. ``unstd.rex`` is the case that needs it: it degrades to stdlib
    ``re`` per *pattern*, so the only honest question is what it returned for
    this pattern — which also survives the backend being renamed underneath us."""

    @property
    def available(self) -> bool:
        if self.probe is not None:
            return self.probe()
        return importlib.util.find_spec(self.backend) is not None


_REGISTRY: list[Case] = []


def case(**kw: Any) -> None:
    """Register one case."""
    _REGISTRY.append(Case(**kw))


def registry(only: Sequence[str] = ()) -> list[Case]:
    """Every registered case, optionally narrowed to some groups."""
    return [c for c in _REGISTRY if not only or c.group in only]


def groups() -> list[str]:
    return sorted({c.group for c in _REGISTRY})


# ---------------------------------------------------------------- payloads


def _records(n: int) -> list[dict[str, Any]]:
    """Wire-shaped JSON — the payload an API actually serializes."""
    rng = random.Random(0xC0FFEE)
    return [
        {
            "id": i,
            "name": f"record-{i:05d}",
            "score": round(rng.random(), 6),
            "tags": [f"t{rng.randrange(50)}" for _ in range(4)],
            "meta": {"active": bool(i % 3), "rank": rng.randrange(1000)},
        }
        for i in range(n)
    ]


def _prose(n: int) -> str:
    rng = random.Random(0xBEEF)
    words = ("gateway", "substrate", "timestamp", "encode", "fallback", "kernel")
    return " ".join(rng.choice(words) + str(rng.randrange(999)) for _ in range(n))


def _names(n: int) -> list[str]:
    rng = random.Random(0xFEED)
    first = ("griffin", "morgan", "avery", "quinn", "harper", "rowan")
    last = ("strier", "nakamura", "okonkwo", "vasquez", "lindqvist")
    return [f"{rng.choice(first)} {rng.choice(last)} {i}" for i in range(n)]


_RECORDS_500 = _records(500)
_RECORDS_JSON = json.dumps(_RECORDS_500, separators=(",", ":"))
_JSON_TEXT = {
    "text": "Exact JSON values and formatting with native acceleration " * 2048
}
_JSON_INTS = json.dumps(list(range(1000))).encode()
_BLOB = bytes(range(256)) * 512  # 128 KiB — the size an upload chunk lands at
_B64_TEXT = base64.b64encode(_BLOB).decode("ascii")
_URL_B64_TEXT = base64.urlsafe_b64encode(_BLOB).decode("ascii")
_JSON_INT_RECORD = {"id": 7, "name": "small", "ok": True, "values": list(range(32))}
_JSON_NESTED = json.dumps(
    [{"id": i, "name": "record", "tags": ["a", "b"]} for i in range(20)]
)
_NESTED = {"items": _records(200)}
_SMALL = {"id": 7, "name": "small", "ok": True}
_INTEGER_SET = set(range(1000))
_DISTANCE_STRINGS = ("abcd" * 256, "bdac" * 256)


@dataclass
class _Hit:
    url: str
    score: float
    seen: datetime.datetime


@dataclass
class _Receipt:
    tool: str
    args: dict[str, Any]
    hits: list[_Hit]


_SEEN = datetime.datetime(2026, 9, 27, tzinfo=datetime.UTC)
_DATACLASS_TREE = _Receipt(
    "search",
    {"q": "lisbon", "k": 8, "filters": ["news", "maps"]},
    [_Hit(f"https://example.com/{i}", 0.9 - i / 10, _SEEN) for i in range(8)],
)


def _models(n: int) -> list[Any]:
    """A chat history's worth of pydantic models — built lazily; pydantic is optional."""
    from pydantic import BaseModel

    class Call(BaseModel):
        name: str
        args: dict[str, Any]

    class Message(BaseModel):
        role: str
        content: str
        calls: list[Call] = []

    return [
        Message(
            role="assistant",
            content=f"turn {i} " * 8,
            calls=[Call(name="search", args={"q": [i]})],
        )
        for i in range(n)
    ]


_HAYSTACK = _prose(4_000)
_CHOICES = _names(2_000)
_QUERIES = _names(20)
_FLOATS = [i * 0.5 for i in range(50_000)]
_PACKED = struct.pack(f"<{len(_FLOATS)}d", *_FLOATS)
_ARRAY = unpack_array("<f8", _PACKED)
_HASH_BLOB = bytes(range(256)) * 4_096  # 1 MiB
_PATTERN = r"\b(gateway|substrate)\d{2,3}\b"
# The case RE2 exists for: stdlib `re` backtracks exponentially on this, so the
# ratio doubles for every character added to the haystack.
# 22 is chosen, not arbitrary: stdlib `re` takes ~0.3 s here and ~4.5 s at 26,
# so a longer haystack would make the default run cost more than the point is
# worth. RE2 is flat regardless.
_EVIL, _EVIL_PATTERN = "a" * 22 + "b", "(a+)+$"


# ---------------------------------------------------------------- serde

case(
    name="jsonx.dumps · 500 records",
    group="serde",
    backend="json",
    payload=lambda: _RECORDS_500,
    accel=jx_dumps,
    base=json.dumps,
    note="float-bearing tree uses native stdlib; fallback overhead reference",
)
case(
    name="jsonx.loads · 500 records",
    group="serde",
    backend="msgspec",
    payload=lambda: _RECORDS_JSON,
    accel=jx_loads,
    base=json.loads,
    note="native decode with exact integers and correctly rounded floats",
)
case(
    name="jsonx.dumps · text payload",
    group="serde",
    backend="orjson",
    payload=lambda: _JSON_TEXT,
    accel=jx_dumps,
    base=json.dumps,
    note="guarded native encode; byte-identical stdlib defaults",
)
case(
    name="jsonx.loads · 1000 integers",
    group="serde",
    backend="msgspec",
    payload=lambda: _JSON_INTS,
    accel=jx_loads,
    base=json.loads,
    note="native exact integer construction; no result-validation walk",
)
case(
    name="b64.encode · 128 KiB",
    group="serde",
    backend="pybase64",
    payload=lambda: _BLOB,
    accel=b64_encode,
    base=lambda p: base64.b64encode(p).decode("ascii"),
    note="pybase64 SIMD vs stdlib base64",
)
case(
    name="b64.decode · 128 KiB",
    group="serde",
    backend="pybase64",
    payload=lambda: _B64_TEXT,
    accel=b64_decode,
    base=lambda p: base64.b64decode(p, validate=True),
    note="strict decoding on both paths; stdlib handles native errors",
)
case(
    name="b64.decode URL · 128 KiB",
    group="serde",
    backend="pybase64",
    payload=lambda: _URL_B64_TEXT,
    accel=lambda p: b64_decode(p, url=True),
    base=base64.urlsafe_b64decode,
    note="padded URL alphabet; identical stdlib results before native timing",
)
case(
    name="jsonx.dumps · 32-int record",
    group="serde",
    backend="orjson",
    payload=lambda: _JSON_INT_RECORD,
    accel=jx_dumps,
    base=json.dumps,
    note="bounded snapshot proof and fallback; byte-identical stdlib spacing",
)
case(
    name="jsonx.dumpb · 500 records",
    group="serde",
    backend="json",
    payload=lambda: _RECORDS_500,
    accel=jx_dumpb,
    base=lambda p: json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode(),
    note="float-bearing fallback transport; one eligibility proof per call",
)
case(
    name="jsonx.loads · 20 nested records",
    group="serde",
    backend="msgspec",
    payload=lambda: _JSON_NESTED,
    accel=jx_loads,
    base=json.loads,
    note="native nested-container decode without a result-validation walk",
)


# ---------------------------------------------------------------- clone
# These payloads are the ones the clone README's table reports, so the table can
# be regenerated instead of remembered. The walker is pure stdlib, so the
# backend these name is `unstd` itself (always importable); the pydantic case
# still needs pydantic installed to have anything to clone.

case(
    name="clone.deep · nested (200 items)",
    group="clone",
    backend="unstd",
    payload=lambda: _NESTED,
    accel=deep,
    base=copy.deepcopy,
    note="exact-type walk vs copy.deepcopy",
)
case(
    name="clone.deep · 500 records",
    group="clone",
    backend="unstd",
    payload=lambda: _RECORDS_500,
    accel=deep,
    base=copy.deepcopy,
)
case(
    name="clone.deep · small dict",
    group="clone",
    backend="unstd",
    payload=lambda: _SMALL,
    accel=deep,
    base=copy.deepcopy,
    note="the floor case — the one a serialization round-trip lost",
)
case(
    name="clone.deep · 1k integer set",
    group="clone",
    backend="unstd",
    payload=lambda: _INTEGER_SET,
    accel=deep,
    base=copy.deepcopy,
    note="atomic set members retain their identity without a forwarding frame",
)
case(
    name="clone.deep · 40 pydantic models",
    group="clone",
    backend="pydantic",
    payload=lambda: _models(40),
    accel=deep,
    base=copy.deepcopy,
    note="mirrors BaseModel.__deepcopy__ with the walker in place of deepcopy",
)
case(
    name="clone.asdict · dataclass tree",
    group="clone",
    backend="unstd",
    payload=lambda: _DATACLASS_TREE,
    accel=clone_asdict,
    base=dataclasses.asdict,
    note="datetime leaves are where dataclasses.asdict pays deepcopy per leaf",
)


# ---------------------------------------------------------------- rex


def _accelerated(pattern: str) -> Callable[[], bool]:
    """A probe for whether ``rex`` left stdlib ``re`` for *this* pattern.

    ``rex`` degrades per pattern, not per install, so an importable backend is
    not evidence: a pattern the engine rejects comes back as an ``re.Pattern``
    and both sides of the comparison become the same function.
    """
    return lambda: not isinstance(rex_compile(pattern), re.Pattern)


case(
    name="rex.findall · 4k words",
    group="rex",
    backend="irgx",
    payload=lambda: _HAYSTACK,
    accel=lambda p: rex_findall(_PATTERN, p),
    base=lambda p: re.findall(_PATTERN, p),
    note="linear-time engine vs stdlib re on a pattern both handle linearly",
    probe=_accelerated(_PATTERN),
)
case(
    name="rex.search · catastrophic backtrack",
    group="rex",
    backend="irgx",
    payload=lambda: _EVIL,
    accel=lambda p: rex_search(_EVIL_PATTERN, p),
    base=lambda p: re.search(_EVIL_PATTERN, p),
    note="22 a's — stdlib re is exponential here; not a like-for-like speedup",
    equivalent=False,
    probe=_accelerated(_EVIL_PATTERN),
)


# ---------------------------------------------------------------- text

case(
    name="fuzz.distance fallback · 1024 characters",
    group="text",
    backend="unstd",
    payload=lambda: _DISTANCE_STRINGS,
    accel=lambda p: _levenshtein(*p),
    base=lambda p: _sequence_distance(*p),
    note="portable bit-vector DP vs classic two-row DP; same Levenshtein cost",
)
case(
    name="fuzz.ratio · one pair",
    group="text",
    backend="rapidfuzz",
    payload=lambda: ("griffin strier", "griffn stier"),
    accel=lambda p: ratio(*p),
    base=lambda p: difflib.SequenceMatcher(None, *p).ratio() * 100,
    note="rapidfuzz Indel SIMD vs difflib SequenceMatcher (different metrics)",
    equivalent=False,
)
case(
    name="fuzz.extract · 1 × 2000",
    group="text",
    backend="rapidfuzz",
    payload=lambda: _CHOICES,
    accel=lambda p: extract("griffin strier", p, limit=5),
    base=lambda p: difflib.get_close_matches("griffin strier", p, n=5, cutoff=0.0),
    note="one query against 2 000 choices",
    equivalent=False,
)
case(
    name="fuzz.cdist · 20 × 2000",
    group="text",
    backend="rapidfuzz",
    payload=lambda: (_QUERIES, _CHOICES),
    accel=lambda p: cdist(p[0], p[1]),
    base=lambda p: [
        [difflib.SequenceMatcher(None, q, c).ratio() * 100 for c in p[1]] for q in p[0]
    ],
    note="the whole 40 000-cell matrix in one call — entity resolution's hot loop",
    equivalent=False,
)


# ---------------------------------------------------------------- rand

case(
    name="draw.ints · 100k",
    group="rand",
    backend="numpy",
    payload=lambda: 100_000,
    accel=lambda n: ints(n, 0, 1 << 20),
    base=lambda n: [random.randrange(1 << 20) for _ in range(n)],
    note="numpy PCG64 bulk draw vs a random.randrange loop",
    equivalent=False,
)
case(
    name="draw.floats · 100k",
    group="rand",
    backend="numpy",
    payload=lambda: 100_000,
    accel=floats,
    base=lambda n: [random.random() for _ in range(n)],
    note="returns a float64 ndarray where the stdlib loop builds a list",
    equivalent=False,
)


# ---------------------------------------------------------------- pack

# Two cases, not one, because `pack_array` has a real crossover and one number
# would hide it. Unboxing 50 000 Python floats is the whole cost of the encode,
# and `struct.pack` unboxes them in a tighter C loop than `numpy.asarray` does —
# so handed a list, NumPy *loses*. Handed an array it already owns, it wins by
# two orders of magnitude, because `tobytes` is a memcpy and struct has to go
# back through Python scalars. Reporting both is what tells a caller which shape
# to hold their data in; reporting only the flattering one would be a lie of
# omission and reporting only the list case would bury the actual value-add.
case(
    name="binpack.pack_array · 50k f64 ← list",
    group="pack",
    backend="numpy",
    payload=lambda: _FLOATS,
    accel=lambda p: pack_array("<f8", p),
    base=lambda p: struct.pack(f"<{len(p)}d", *p),
    note="from a Python list, asarray's unboxing costs more than struct.pack",
)
case(
    name="binpack.pack_array · 50k f64 ← ndarray",
    group="pack",
    backend="numpy",
    payload=lambda: _ARRAY,
    accel=lambda p: pack_array("<f8", p),
    base=lambda p: struct.pack(f"<{len(p)}d", *p.tolist()),
    note="from an ndarray it is a memcpy — this is the seam's actual value",
)
case(
    name="binpack.unpack_array · 50k f64",
    group="pack",
    backend="numpy",
    payload=lambda: _PACKED,
    accel=lambda p: unpack_array("<f8", p),
    base=lambda p: struct.unpack(f"<{len(p) // 8}d", p),
    note="returns an ndarray where struct returns a tuple",
    equivalent=False,
)


# ---------------------------------------------------------------- ids

# The twin is blake2b, not sha256. BLAKE2 is what hashlib ships from the BLAKE
# family, so it is the thing `digest.raw` actually stands in for — and a fair fight.
# sha256 is *not* one: every ARMv8 and modern x86 core implements it in silicon,
# so `hashlib.sha256` here is a hardware instruction and beats single-threaded
# BLAKE3 (350 µs vs 453 µs on an M-series laptop). That is a fact about the chip,
# not about the algorithm, and benchmarking against it would report a loss the
# library cannot fix and a caller cannot act on.
case(
    name="digest.raw · 1 MiB",
    group="ids",
    backend="blake3",
    payload=lambda: _HASH_BLOB,
    accel=digest_raw,
    base=lambda p: hashlib.blake2b(p).digest(),
    note="BLAKE3 vs hashlib's BLAKE2b — the stdlib's own BLAKE-family digest",
    equivalent=False,
)


# ---------------------------------------------------------------- equivalence


@dataclass(frozen=True, slots=True)
class Divergence:
    """A case whose two sides disagreed on the answer."""

    case: str
    detail: str


def audit(cases: Sequence[Case]) -> list[Divergence]:
    """Check that every ``equivalent`` case's two sides really do agree.

    Comparing two functions that return different things measures nothing, and
    it is the easiest mistake to make here — one wrong ``separators`` argument
    or a mismatched dtype turns a rigorous ratio into a fabricated one. So the
    harness proves equivalence on every ``run`` rather than parking the check in
    a test nobody invokes.
    """
    out: list[Divergence] = []
    for c in cases:
        if not (c.equivalent and c.available):
            continue
        payload = c.payload()
        left, right = c.accel(payload), c.base(payload)
        if left != right:
            out.append(Divergence(c.name, f"{str(left)[:80]!r} != {str(right)[:80]!r}"))
    return out


# ---------------------------------------------------------------- typed NDJSON


@dataclass(frozen=True, slots=True)
class _TraceUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class _TraceRow:
    event_id: str
    timestamp: str
    name: str
    duration_ms: float
    success: bool
    tags: list[str]
    attributes: dict[str, str]
    usage: _TraceUsage


@cache
def _trace_readers() -> tuple[Codec[_TraceRow], Callable[[bytes], _TraceRow]]:
    """Bind one reusable codec and the equivalent dict-then-validate reader."""
    from msgspec import convert

    from unstd.serde.structs import Codec

    def through_dict(line: bytes) -> _TraceRow:
        return convert(jx_loads(line), type=_TraceRow)

    return Codec(_TraceRow), through_dict


def _trace_payload() -> bytes:
    """One thousand UTF-8 trace rows, prepared outside the timed region."""
    codec, _ = _trace_readers()
    return ndjson.dumpb(
        (
            _TraceRow(
                event_id=f"event-{i:05d}",
                timestamp="2026-10-18T12:34:56.123456Z",
                name="search_documents",
                duration_ms=12.5 + i % 240 / 10,
                success=i % 31 != 0,
                tags=["production", "retrieval"],
                attributes={"service": "agent", "query": "recent café documents"},
                usage=_TraceUsage(1_200 + i % 400, 80 + i % 90),
            )
            for i in range(1_000)
        ),
        codec=codec,
    )


def _typed_trace_rows(payload: bytes) -> list[_TraceRow]:
    """Decode directly into the reusable codec's validated dataclass schema."""
    codec, _ = _trace_readers()
    return ndjson.loads(payload, codec=codec)


def _validated_trace_rows(payload: bytes) -> list[_TraceRow]:
    """Decode dictionaries, then validate each into the same dataclass schema."""
    _, decode = _trace_readers()
    return [
        decode(line.strip())
        for line in payload.split(b"\n")
        if line and not line.isspace()
    ]


case(
    name="ndjson.loads · 1k typed trace rows",
    group="serde",
    backend="msgspec",
    payload=_trace_payload,
    accel=_typed_trace_rows,
    base=_validated_trace_rows,
    note="reused typed codec vs validated dict-then-convert; identical dataclass results",
    equivalent=True,
)


# ---------------------------------------------------------------- gzip


@cache
def _gzip_trace_rows() -> list[dict[str, Json]]:
    """Four thousand trace records shared by codec and stream measurements."""
    return [
        {
            "event_id": f"event-{i:05d}",
            "timestamp": "2026-10-18T12:34:56.123456Z",
            "name": "search_documents",
            "duration_ms": 12.5 + i % 240 / 10,
            "success": i % 31 != 0,
            "tags": ["production", "retrieval"],
            "attributes": {"service": "agent", "query": "recent café documents"},
            "usage": {"input_tokens": 1_200 + i % 400, "output_tokens": 80 + i % 90},
        }
        for i in range(4_000)
    ]


def _gzip_compress(payload: bytes) -> bytes:
    """Compress with an explicit level and reproducible timestamp."""
    return gzipx.compress(payload, compresslevel=6, mtime=0)


def _stdlib_gzip_compress(payload: bytes) -> bytes:
    """The same compression settings on stdlib's codec."""
    return gzip.compress(payload, compresslevel=6, mtime=0)


@cache
def _gzip_trace_bytes() -> bytes:
    """Prepare JSONL and prove both compression outputs recover it exactly."""
    payload = ndjson.dumpb(_gzip_trace_rows())
    native, standard = _gzip_compress(payload), _stdlib_gzip_compress(payload)
    assert gzip.decompress(native) == payload
    assert gzipx.decompress(standard) == payload
    return payload


@cache
def _gzip_trace_compressed() -> bytes:
    """One identical stdlib-compressed input for both decompression codecs."""
    compressed = _stdlib_gzip_compress(_gzip_trace_bytes())
    assert gzipx.decompress(compressed) == gzip.decompress(compressed)
    return compressed


def _gzip_archive(rows: Iterable[object], factory: type[gzip.GzipFile]) -> bytes:
    """Stream NDJSON into one gzip member; expose bytes for size comparisons."""
    stream = io.BytesIO()
    with factory(fileobj=stream, mode="wb", compresslevel=6, mtime=0) as compressed:
        ndjson.dump(rows, compressed)
    return stream.getvalue()


def _gzip_roundtrip(
    rows: list[dict[str, Json]], factory: type[gzip.GzipFile]
) -> list[Json]:
    """Measure the complete NDJSON dump, gzip write, gzip read, NDJSON load."""
    archive = _gzip_archive(rows, factory)
    with factory(fileobj=io.BytesIO(archive), mode="rb") as compressed:
        return ndjson.load(compressed)


def _native_gzip_roundtrip(rows: list[dict[str, Json]]) -> list[Json]:
    """The complete pipeline with native compression and inflation."""
    return _gzip_roundtrip(rows, gzipx.GzipFile)


def _stdlib_gzip_roundtrip(rows: list[dict[str, Json]]) -> list[Json]:
    """The same pipeline using stdlib gzip in both directions."""
    return _gzip_roundtrip(rows, gzip.GzipFile)


case(
    name="gzipx.compress · 4k JSONL trace rows",
    group="compress",
    backend="zlib_ng",
    payload=_gzip_trace_bytes,
    accel=_gzip_compress,
    base=_stdlib_gzip_compress,
    note="level 6, mtime 0; compressed bytes/sizes may differ; recovered bytes checked",
    equivalent=False,
)
case(
    name="gzipx.decompress · 4k JSONL trace rows",
    group="compress",
    backend="zlib_ng",
    payload=_gzip_trace_compressed,
    accel=gzipx.decompress,
    base=gzip.decompress,
    note="identical stdlib-compressed input; both return the original JSONL bytes",
)
case(
    name="gzipx + ndjson · 4k-row stream roundtrip",
    group="compress",
    backend="zlib_ng",
    payload=_gzip_trace_rows,
    accel=_native_gzip_roundtrip,
    base=_stdlib_gzip_roundtrip,
    note="level 6, mtime 0; complete in-memory write/read pipeline; equal recovered rows",
)
