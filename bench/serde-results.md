# Serde function audit

Measured on 2026-10-18 using CPython 3.12.13, macOS arm64, Apple M5 Max,
orjson 3.12.0, msgspec 0.22.0, pybase64 1.5.0 and NumPy 2.5.3. The starting
Serde source matches Git revision `20e43cc4b2c06ab2dacec110255ffd8589c8dfd5`.
Unrelated working-tree edits were excluded from the comparison.

All **42 runtime Python functions**, including nested closures and constructors,
were exercised by **122 output-checked workloads**. The starting source has
41 functions; inlining NDJSON writer setup adds a second local `line` closure.
Stdlib aliases and the two directly bound native Codec methods are exercised
as workloads too. Protocol and overload declarations have no runtime work.
Public callable signatures match the starting source, including defaults and
annotations. The per-function counts, self/cumulative profile times and every
workload comparison are in [`serde-results.json`](serde-results.json).

The workload matrix covers tiny scalars, short/wide/nested JSON, large text and
float-bearing fallback records; compact, sorted and indented formatting;
callbacks, errors, NumPy conversion and streams; Base64 bytes/text at 0, 16,
128, 1,024 and 131,072 decoded bytes, padded/unpadded URL text and dirty input;
typed codecs, generic codecs, and all NDJSON read/write APIs.

## Recorded measurements

Native-enabled results; costs are microseconds per operation. The last two
rows show unchanged/native and stream controls, not claimed optimizations.

| Workload | Before | After | Before/after |
|---|---:|---:|---:|
| JSON dumps, scalar string | 0.225 | 0.139 | 1.62× |
| JSON loads, small record | 1.013 | 0.772 | 1.31× |
| JSON load, in-memory text stream | 0.720 | 0.561 | 1.28× |
| JSON dumpb, 15 integers | 1.230 | 0.938 | 1.31× |
| Base64 decode, 5-byte result | 0.271 | 0.088 | 3.08× |
| Base64 strict decode, 16-byte result | 0.221 | 0.104 | 2.12× |
| Base64 URL text decode, 128-byte result | 0.587 | 0.349 | 1.68× |
| Base64 text strict decode, 128 KiB result | 13.404 | 11.424 | 1.17× |
| Native typed encode, unchanged control | 0.057 | 0.057 | 1.00× |
| Typed NDJSON dumpb, 100 rows | 6.590 | 6.487 | 1.02× |

Forced fallback also improved short standard Base64 decoding and default JSON
string encoding. The JSON file retains all cases, including unchanged and
slower observations, per-repetition samples, and batch spreads. Some batches
were noisy; several untouched methods moved too, so small differences are
not evidence of an implementation gain or loss. The four integer-array cases
were added and measured in a supplemental paired batch with identical settings.

## Validation

- CPython 3.12.13 full repository suite: **6,029 passed**, 6 skipped,
  165 explicitly deselected performance/chaos cases.
- CPython 3.13.14 and 3.14.6: **2,743 Serde tests passed on each** with orjson,
  msgspec, pybase64 and NumPy installed, including forced-fallback parity cases.
- Repository Ruff checks/formatting, strict mypy, suite inventory and diff
  whitespace checks pass. Public signature comparisons include annotations.
- All **11 existing Serde floors passed** with 21 rounds and no skipped
  backends. A preceding seven-round verify transiently missed the two large
  encoding fallback floors; the longer rerun still flagged scheduling noise.
  No floors were changed. This is not quiet-worker certification.
- Added compatibility checks cover Base64 cutoff/coercion/error-context behavior,
  decoded-tree boundaries and NDJSON callback/iterator/error behavior. The
  profile harness checks output types/order, import-source identity, restored
  module namespaces, and declaration-free function inventory.

## Changes retained

| Functions | Decision |
|---|---|
| `jsonx.dumps` | Default calls use identity checks and a prebound stateless encoder, avoiding format classification and cache lookup. Other options retain their exact eligibility checks. |
| `jsonx.load` | Default reads avoid forwarding seven explicit default keywords to `loads`. Reads still happen once. |
| `_json_encode._snapshot` | Bulk integer checks use a direct loop, removing a generator allocation/frame. Private copies, node/depth budgets, integer bounds and the recursive scalar probe remain. |
| `_json_decode._decoded_exact` | Scalars return directly; short containers avoid map/set allocations; the work stack retains containers rather than scalar siblings. Large flat trees retain their bulk C-level type check. |
| `b64.b64encode`, `standard_b64encode` | No-alternate-alphabet fallback uses stdlib's same `binascii` primitive directly; standard encoding avoids a wrapper call. |
| `b64.b64decode`, `standard_b64decode`, `decode` | Short exact bytes/ASCII strings use `binascii`; strict native decoding begins at 256 encoded characters. Subclasses, non-ASCII strings, other alphabets/options and failures retain authoritative stdlib behavior. |
| `b64.decode` URL path | Exact bytes avoid coercion; clean exact ASCII strings reach native decoding before a Python-level bytes copy. Failed attempts are not repeated before normalization. |
| `ndjson.dumpb` | Writer setup binds directly, retaining the per-record map and newline semantics. |

## Implementations retained after profiling

`_json_encode._format`, `_exact_encode`, `_exact_bytes`, `_encoder` and
`_compact_bytes` retain bounded eligibility, exact output, a bounded encoder
cache and the fallback recursion frame. Default calls now avoid `_format`
and `_encoder` where they are unnecessary.

`_json_decode._decode_eligible` keeps its bounded source scan: simpler counting
variants regressed large flat and byte inputs. `_verify_result` keeps the
second validation frame required to preserve stdlib recursion failures.

`jsonx.dumpb`, `_line`, `canonical`, `loads`, `_numpy_default` and its `_default`
closure retain their output, callback, snapshot and error policies. `dump`
continues to use stdlib streaming because write chunk boundaries and partial
output before failure are observable.

`b64.encode`, `urlsafe_b64encode`, `_decode_input`, `decode_text`, `encode_json`
and `decode_json` already use native methods or the required coercion/composition.
MIME/file helpers and `urlsafe_b64decode` remain direct stdlib aliases.

`ndjson.dumps`, `_encoder` and its `line` closure, `iter_loads`, `loads`, `dump`,
`load` and `NDJSONDecodeError.__init__` retain newline, physical line numbering,
lazy iteration, callback, typed validation and error semantics. Binding writes
or changing loop counters did not establish a consistent benefit.

`structs.Codec.__init__` already binds `encode` and `decode` directly to native
methods. `decode_or` retains only the required decode-error handling.
`float_enc_hook` retains its `SupportsFloat` acceptance check: replacing it
changed behavior or measured slower.

A native NDJSON `encode_lines` rewrite was rejected despite faster ordinary
records. A `StopIteration` raised while encoding by an encoder hook, callable
decimal policy or timezone callback historically terminates `map`, returning
the completed prefix. `encode_lines` propagates it and hides its partial output.
Retrying could repeat callbacks. Regression tests retain the original prefix
and consumption behavior.

## Measurement method

Function coverage and call costs use cProfile in isolated source workers.
Workload timings run separately, without the profiler, with GC disabled only
during batches and restored afterward. Each measurement uses the fastest of
nine calibrated batches. Before/after timings alternate order over three
repetitions for each workload; the reported costs are medians of those three
measurements. The two implementations use separate module namespaces in one
interpreter, with source paths checked and the original import namespace
restored afterward. This limits scheduler/clock drift seen in longer separate
process runs, including apparent changes in untouched native methods.

Forced fallback disables orjson/pybase64 dispatch, while typed msgspec codecs
remain available. Its six unused native eligibility helpers are intentionally
unexecuted; the complete native run covers them. Profiler times include
instrumentation and aggregate different workloads, so they locate work rather
than establish uninstrumented per-call latency.

Reproduce after extracting the starting source into `/tmp/serde-before/src`:

```console
python -m bench.serde_profile --compare-source /tmp/serde-before/src --rounds 9 --repetitions 3 --output /tmp/serde-profiles.json
python -m bench.serde_profile --compare-source /tmp/serde-before/src --paired --rounds 9 --repetitions 3 --output /tmp/serde-paired.json
python -m bench.serde_profile --compare-source /tmp/serde-before/src --paired --fallback --rounds 9 --repetitions 3 --output /tmp/serde-paired-fallback.json
```

These measurements do not prove universal optimality or eliminate the cost of
the compatibility contract. Inputs, interpreter/backend versions and hardware
can change which implementation wins. Unchanged methods serve as noise controls;
small timing differences are not claimed as improvements.
