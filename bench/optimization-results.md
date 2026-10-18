# Runtime optimization measurements

Measured on macOS arm64 with CPython 3.12.13 on 2026-10-18. “Before” is the
working implementation at the start of this optimization pass, including its
existing uncommitted changes. Both versions ran in the same interpreter against
the same payloads; successful outputs were compared before timing. These are
workload-specific improvements over the previous implementation.

Installed backends: orjson 3.12.0, pybase64 1.5.0, NumPy 2.5.3,
RapidFuzz 3.14.6 and Pydantic 2.13.5.

| Operation and payload | Before | After | Improvement |
|---|---:|---:|---:|
| URL Base64 decode, padded 1 MiB bytes | 1,710 µs | 191 µs | 8.9× |
| JSON dumps, record with 32 integers | 5.39 µs | 2.92 µs | 1.85× |
| JSON dumpb, same record | 3.98 µs | 2.61 µs | 1.52× |
| JSON loads, 20 nested records | 13.47 µs | 5.16 µs | 2.61× |
| Portable edit distance, two 1,024-character strings | 105,534 µs | 768 µs | 137× |
| Deep clone, set of 1,000 integers | 33.30 µs | 19.51 µs | 1.71× |
| Deep clone, 40 Pydantic models | 84.69 µs | 68.76 µs | 1.23× |
| Stdlib pack fallback, eight int32 values | 0.562 µs | 0.235 µs | 2.39× |
| Stdlib pack fallback, 65,536 int32 values | 604 µs | 355 µs | 1.70× |

The JSON, clone, pack and edit-distance comparisons alternate old/new order over
three paired repetitions and use the minimum batch cost, with 5–11 rounds per
measurement through `bench.timing.measure`. Base64 uses the minimum of seven
25-call batches. The shared machine was noisy; absolute timings and exact
ratios should be remeasured on a quiet worker. Common small/string-key dictionary
cloning remained approximately unchanged, as did bulk NumPy packing.

The JSON record is `{"id": 7, "name": "small", "ok": True, "values": list(range(32))}`.
Nested JSON uses `[{'id': i, 'name': 'record', 'tags': ['a', 'b']} for i in range(20)]`.
Base64 encodes `bytes(range(256)) * 4096` with `base64.urlsafe_b64encode`.
Edit distance compares `"abcd" * 256` and `"bdac" * 256`. The clone model workload
is the existing harness case. Packing uses `"<i4"` and `list(range(n))`, with
the NumPy capability disabled in the comparison process.

Six new audited cases in `bench.cases` cover URL Base64, JSON record encoding,
compact fallback encoding, nested decoding, integer-set cloning and portable
edit distance. Run them with the other relevant cases:

```console
uv run --no-sync python -m bench --group serde --group clone --group text --rounds 11
```

The harness compares current code with independent stdlib operations, or the
classic two-row edit-distance fallback. Its ratios therefore differ from the
before/after table above. JSON fallback cases can still measure below stdlib
parity because they preserve the guarded codec surface. New floors use 70% of
their measured harness ratios; existing floors were preserved when adding them.

Public signatures, wire formats and backend dependencies are unchanged.
Regressions cover subclass hooks, custom metaclasses, input mutation, integer
bounds, recursion headroom, padding, Unicode and arbitrary sequence elements.
The edit-distance state is masked to the shorter string's width, and character
mask count is bounded to avoid quadratic storage on diverse Unicode inputs.

## Package startup

On the same macOS arm64 / CPython 3.12.13 host with msgspec 0.22.0, deferring
`unstd.__version__` lookup removes distribution discovery from codec imports.
`Codec` still caches the native encoder and decoder methods; its implementation
and parsing policy are unchanged.

| Fresh-process operation | Eager version lookup | Lazy version lookup | Direct msgspec |
|---|---:|---:|---:|
| Import, construct decoder, decode first record | 24.48 ms | 12.66 ms | 11.86 ms |
| Complete child process | 42.69 ms | 27.75 ms | 27.75 ms |

These are medians of 31 samples per variant, after two discarded warmups, with
variant order rotated between rounds. Each sample launches a new isolated
interpreter (`python -I`), adds the selected source tree to `sys.path`, then
times the import, decoder construction and first decode with `perf_counter_ns`.
The parent separately times the complete subprocess. The before tree is a copy
of the source before this change; the after tree uses the working source.
The wrapper path is `from unstd.serde import structs` followed by
`structs.Codec(dict[str, object]).decode`; the direct path uses
`msgspec.json.Decoder(dict[str, object]).decode`. Both decode
`'{"n":1,"s":"café","items":[true,null,2.5]}'` and assert the same result,
native decoder type and schema. Import-plus-first-decode improves **1.93×** and
complete process time **1.54×** in this local sample. These timings do not
establish an application-specific storage or SQL performance gate.

For a fresh import profile, run:

```console
uv run --no-sync python -X importtime -c 'from unstd.serde.structs import Codec; Codec(dict[str, object]).decode("{}")'
```

`importlib.metadata` should be absent from that profile. Ordinary correctness
tests enforce this by blocking its import in fresh workers, including installed
wheel and sdist-built-wheel consumers. They also check native decoder reuse and
successful/invalid-input parity against msgspec. Version access still resolves
installed metadata on demand and caches the string, retaining the source-tree
fallback and refreshing lazily after `importlib.reload`.
