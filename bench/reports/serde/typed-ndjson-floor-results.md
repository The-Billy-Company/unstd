# Typed NDJSON relative-floor diagnosis

Measured 2026-10-21 on macOS 26.6 arm64, CPython 3.13.14 (Clang 22.1.3),
with orjson 3.12.0, msgspec 0.22.0 and NumPy 2.5.3.

**The existing relative floor still fails.** This run measured 1.73× against
its committed 2.71× floor. The paired comparison attributes the large ratio
change to a faster dictionary-decoding comparator. No meaningful typed-reader
slowdown is established by this small sample; it also does not prove zero
slowdown. No floor, comparator, or production implementation was changed for
this diagnosis.

## Sources and semantics

The historical source is commit
`4adee0a7ca57e1fa58fc11f778ec1dd835a3b708`, which introduced this case and its
3.87× measurement / 2.71× floor on CPython 3.12. The current snapshot starts at
`fedb4d0109488816fbba76614146d93e2f423780` and includes the concurrent
`structs.py` documentation correction; its measured source hashes are below.
Historical and current Python package trees were frozen into separate temporary
directories, loaded into distinct retained module graphs, and their import paths
were checked. Both ran in one interpreter with the same installed backends.

The original benchmark's `_TraceUsage`, `_TraceRow`, `_trace_readers`,
`_trace_payload`, `_typed_trace_rows` and `_validated_trace_rows` definitions are
AST-identical between sources, as is `ndjson.iter_loads`. Each source ran its own
original benchmark functions and reusable codec. Both produced the same 1,000-row,
278,803-byte payload, SHA-256
`81ee49ba6905ea7059c70c519c3a851266bc74d57d0de7edbb5230004683979c`.
Within each graph, typed and dictionary paths returned equal nested dataclass
instances with the exact declared row/usage types. Re-encoding those results
also matched across the two distinct class namespaces.

## Paired results

Fifteen neighboring old/current batches per path, eight complete calls per
batch; call order alternated each round. Codec/payload setup and warmup preceded
timing. GC was disabled only during timed batches and its prior state restored.
The primary timer was process CPU time; wall-time medians agreed closely.
Reported ratios are medians of neighboring CPU batch ratios and need not equal
the quotient of separately summarized medians.

| Complete 1,000-row call | Historical CPU µs | Current CPU µs | Current / historical |
| --- | ---: | ---: | ---: |
| Reused typed NDJSON reader | 544.375 | 549.375 | 1.00553 |
| Validated dictionary reader | 2,934.750 | 946.125 | 0.32228 |

The historical dictionary/typed ratio reproduced as **5.37015×** on this
interpreter; the current ratio was **1.72727×**. The checked-in 3.87× result is
from a different interpreter/run and is retained as historical evidence. The
roughly 0.55% paired typed difference is smaller than the few-percent batch
variation here. These bounded shared-machine observations do not establish a
strict regression or no-regression guarantee.

The embedded reproduction was extracted and run once more while other workspace
work was active. Historical/current typed medians were 732.875 / 744.750 µs,
with a paired current/historical ratio of 0.99498; dictionary medians were
4,244.250 / 1,314.125 µs, paired ratio 0.30754. Its current strategy ratio was
1.77398× and its payload, AST/output checks and profile counts matched. The large
absolute movement reinforces the shared-machine limitation; the first run above
was retained rather than selecting a favorable repetition.

A separate untimed cProfile pass explains the changed comparator:

| Dictionary path, one call | Historical calls | Current calls |
| --- | ---: | ---: |
| `orjson.loads` | 1,000 | 0 |
| stdlib `json.loads` | 1,000 | 0 |
| msgspec decoder | 0 | 1,000 |

The historical `jsonx` decoder rejected surviving floats during its exact-value
proof, so every float-bearing trace row took the native parse and then stdlib
fallback. Current `jsonx` decodes the same dictionaries directly through msgspec.
That improves the reference by about 3.1× while the typed ingestion path remains
substantially unchanged.

This case remains useful for choosing between two current ingestion strategies.
Its historical relative floor also moves when the comparator improves, so it is
not an independent typed-throughput regression test. Keep the historical floor
and its current failure visible. Evaluate typed regressions with a separate
paired old/current typed-reader comparison, preserving this comparison and its
historical evidence rather than lowering its floor or slowing its reference.

## Source identity

SHA-256 of measured current files:

| File | SHA-256 |
| --- | --- |
| `bench/cases.py` | `379e6cb223147ab0793fb54ec4801b2b4e6859c5a65f40d428ab2b91379e9622` |
| `src/unstd/serde/ndjson.py` | `e2cb8f588e301ab3d77ee13f74ca854f99c7f52286fef60aeee4567625fe436c` |
| `src/unstd/serde/jsonx.py` | `04f6a3902addfa367d6394ff8e43c37c38bddc74e126fd684b7398838a0d3bea` |
| `src/unstd/serde/structs.py` | `dad3b2373acd93e4d833e61f66b2af8746ef5002efd716095f01c1e6f606c253` |
| CP313 native JSON binary | `0533658776f877fe7b08847f6a5129691accabc6a75483ff83c892a69725094a` |

The native JSON binary was present in the current snapshot, but CPython 3.13
uses the Python `loads` facade and msgspec for these rows. The profiling counts
above describe the measured decode dispatch.

## Recorded CPU batch means

Microseconds per complete call; rows preserve execution-round order.

| Round | Historical typed | Current typed | Historical dictionary | Current dictionary |
| --- | ---: | ---: | ---: | ---: |
| 1 | 556.000 | 556.875 | 2974.625 | 961.875 |
| 2 | 557.125 | 554.625 | 2953.625 | 939.625 |
| 3 | 562.750 | 552.000 | 2976.250 | 938.000 |
| 4 | 538.125 | 543.375 | 2943.500 | 946.125 |
| 5 | 544.375 | 553.750 | 2951.375 | 962.625 |
| 6 | 555.500 | 546.750 | 2932.375 | 961.625 |
| 7 | 547.500 | 551.250 | 2972.625 | 947.500 |
| 8 | 538.000 | 540.750 | 2934.750 | 942.500 |
| 9 | 539.375 | 549.250 | 2918.875 | 957.000 |
| 10 | 543.125 | 549.500 | 2913.375 | 940.000 |
| 11 | 538.250 | 546.250 | 2935.625 | 978.750 |
| 12 | 553.500 | 549.375 | 2928.375 | 943.750 |
| 13 | 541.000 | 538.875 | 2905.250 | 959.500 |
| 14 | 542.875 | 545.875 | 2931.750 | 940.500 |
| 15 | 546.125 | 549.875 | 2930.750 | 934.250 |

## Reproduction

From the repository root, use the stated interpreter and backend versions.
This bounded script freezes the historical commit and current working sources,
checks source binding and valid-output parity, runs the paired sample and
separate profiling pass, and prints a temporary JSON report containing every
CPU/wall sample. It writes only temporary files. Current source edits or different
interpreters/backends produce a new observation, not the recorded one above.

```sh
.venv/bin/python - <<'PY'
from __future__ import annotations

import ast
import cProfile
import gc
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import pstats
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path.cwd().resolve()
DEST = Path(tempfile.mkdtemp(prefix='unstd-typed-ndjson-')).resolve()
BEFORE = DEST / 'before'
BEFORE.mkdir()
archive = subprocess.run(['git', 'archive', '4adee0a', 'src/unstd', 'bench/cases.py'], cwd=ROOT, check=True, capture_output=True).stdout
with tarfile.open(fileobj=io.BytesIO(archive)) as archive_file:
    archive_file.extractall(BEFORE, filter='data')
AFTER = DEST / 'after'
shutil.copytree(ROOT / 'src/unstd', AFTER / 'src/unstd')
(AFTER / 'bench').mkdir()
shutil.copyfile(ROOT / 'bench/cases.py', AFTER / 'bench/cases.py')


def retained(root, label):
    saved = {name: module for name, module in sys.modules.items() if name == 'unstd' or name.startswith('unstd.')}
    saved_path = sys.path[:]
    try:
        for name in saved:
            del sys.modules[name]
        sys.path.insert(0, str(root / 'src'))
        importlib.invalidate_caches()
        spec = importlib.util.spec_from_file_location('_typed_ndjson_' + label, root / 'bench/cases.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        payload = module._trace_payload()
        from unstd.serde import jsonx, ndjson, structs
        assert Path(jsonx.__file__).resolve() == root / 'src/unstd/serde/jsonx.py'
        assert module.ndjson is ndjson
        assert module.jx_loads is jsonx.loads
        codec, _ = module._trace_readers()
        assert codec.__class__ is structs.Codec
        operations = {
            'typed': lambda: module._typed_trace_rows(payload),
            'validated_dict': lambda: module._validated_trace_rows(payload),
        }
        typed = operations['typed']()
        validated = operations['validated_dict']()
        assert typed == validated
        assert len(typed) == 1000
        assert all(type(row) is module._TraceRow and type(row.usage) is module._TraceUsage for row in typed + validated)
        # Native serialization of the typed output supplies a cross-namespace
        # comparison despite the two dataclass definitions being distinct.
        normalized = codec.encode(typed)
        return operations, payload, normalized, jsonx, module
    finally:
        for name in list(sys.modules):
            if name == 'unstd' or name.startswith('unstd.'):
                del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path


loaded = {label: retained(path, label) for label, path in [('before', BEFORE), ('after', AFTER)]}
assert loaded['before'][1] == loaded['after'][1]
assert loaded['before'][2] == loaded['after'][2]
functions = {(label, case): function for label, value in loaded.items() for case, function in value[0].items()}
for function in functions.values():
    function()
iterations = 8
rounds = 15
samples = {f'{label}/{case}': [] for label, case in functions}
gc_was_enabled = gc.isenabled()
gc.disable()
try:
    for round_index in range(rounds):
        for case in ('typed', 'validated_dict'):
            for label in (('before', 'after') if round_index % 2 == 0 else ('after', 'before')):
                function = functions[label, case]
                start_cpu = time.process_time_ns()
                start_wall = time.perf_counter_ns()
                for _ in range(iterations):
                    function()
                wall = time.perf_counter_ns() - start_wall
                cpu = time.process_time_ns() - start_cpu
                samples[f'{label}/{case}'].append({'cpu_us': cpu / iterations / 1000, 'wall_us': wall / iterations / 1000})
finally:
    if gc_was_enabled:
        gc.enable()

profiles = {}
for label in ('before', 'after'):
    profile = cProfile.Profile()
    profile.runcall(functions[label, 'validated_dict'])
    stats = pstats.Stats(profile).stats
    profiles[label] = {
        'stdlib_json_loads_calls': sum(value[1] for (filename, _, function), value in stats.items() if filename.endswith('/json/__init__.py') and function == 'loads'),
        'orjson_loads_calls': sum(value[1] for (filename, _, function), value in stats.items() if filename == '~' and 'orjson.loads' in function),
        'msgspec_decode_calls': sum(value[1] for (filename, _, function), value in stats.items() if filename == '~' and 'decode' in function and 'msgspec' in function),
    }


def median_ratio(numerator, denominator):
    return statistics.median(a['cpu_us'] / b['cpu_us'] for a, b in zip(samples[numerator], samples[denominator], strict=True))


def function_ast(root, filename, names):
    tree = ast.parse((root / filename).read_text())
    return {node.name: ast.dump(node, include_attributes=False) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names}

report = {
    'python': sys.version,
    'platform': platform.platform(),
    'versions': {name: __import__(name).__version__ for name in ('orjson', 'msgspec', 'numpy')},
    'baseline_commit': '4adee0a',
    'source_paths': {'before': str(BEFORE), 'after': str(AFTER)},
    'payload_bytes': len(loaded['before'][1]),
    'payload_sha256': hashlib.sha256(loaded['before'][1]).hexdigest(),
    'same_payload_and_normalized_result': True,
    'ndjson_iter_loads_ast_identical': function_ast(BEFORE, 'src/unstd/serde/ndjson.py', {'iter_loads'}) == function_ast(AFTER, 'src/unstd/serde/ndjson.py', {'iter_loads'}),
    'trace_benchmark_ast_identical': function_ast(BEFORE, 'bench/cases.py', {'_TraceUsage', '_TraceRow', '_trace_readers', '_trace_payload', '_typed_trace_rows', '_validated_trace_rows'}) == function_ast(AFTER, 'bench/cases.py', {'_TraceUsage', '_TraceRow', '_trace_readers', '_trace_payload', '_typed_trace_rows', '_validated_trace_rows'}),
    'rounds': rounds,
    'iterations_per_batch': iterations,
    'median_us': {key: {clock: statistics.median(item[clock] for item in values) for clock in ('cpu_us', 'wall_us')} for key, values in samples.items()},
    'paired_cpu_ratios': {
        'typed_current_over_before': median_ratio('after/typed', 'before/typed'),
        'validated_dict_current_over_before': median_ratio('after/validated_dict', 'before/validated_dict'),
        'historical_speedup_reproduced': median_ratio('before/validated_dict', 'before/typed'),
        'current_speedup': median_ratio('after/validated_dict', 'after/typed'),
    },
    'validated_dict_profile_counts': profiles,
    'samples': samples,
}
output = DEST / 'report.json'
output.write_text(json.dumps(report, indent=2))
print(json.dumps({key: value for key, value in report.items() if key != 'samples'}, indent=2))
print('REPORT', output)
PY
```
