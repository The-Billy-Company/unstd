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
