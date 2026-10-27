# JSON decoding beyond 65,536 values

The native decoder previously discarded a complete `orjson` parse once its
result-validation walk exceeded 65,536 values, then parsed the source again with
`msgspec`. Large arrays and objects paid for both parses even when every value
was already exact.

The proof budget now follows the original source length. Every newly parsed
value needs at least one source byte/code point, and parsed containers cannot
form an alias-expanded graph. Work therefore stays bounded by input size. The
64-container depth cap, stack guards, float/large-integer refusals, callbacks and
stdlib diagnostic fallbacks retain their existing behavior. Encoder proof
budgets are unchanged.

## Measured impact

Measured October 27, 2026, on macOS 26.6 ARM64, CPython 3.14.9, orjson 3.12.0 and
msgspec 0.22.0. Both revisions use active native dispatch. These are synthetic
in-memory workloads; they establish this change's effect on this host, rather
than a universal speed guarantee.

| Public operation and input | Before | After | Paired speedup |
| --- | ---: | ---: | ---: |
| `loads`, 20,000 independent records | 5.846 ms | 2.859 ms | 2.06× |
| `loads`, 100,000 independent records | 33.904 ms | 17.564 ms | 1.96× |
| `loads`, 100,000 integers | 3.253 ms | 1.511 ms | 2.11× |
| `loads`, 65,534 integers plus two object values | 1.014 ms | 0.992 ms | 1.01× |
| `loads`, 65,535 integers plus two object values | 2.199 ms | 1.083 ms | 2.10× |
| `load`, 20,000 records from a text stream | 6.266 ms | 3.471 ms | 1.85× |
| `ndjson.loads`, one array-valued record containing 20,000 records | 6.195 ms | 3.155 ms | 2.02× |

The neighboring integer counts show the old cliff disappearing. Late floats,
signed zero and large integers retain their existing precise-parser fallback;
their paired speedups were 0.98–1.00×. Small str/bytes inputs and long ASCII,
Latin-1, BMP and supplementary-plane text controls measured 0.98–1.01×. All 16
scale workloads passed a maximum 15% slowdown gate.

The unchanged existing throughput profile supplied another 20 decoder controls:
small records, 1,000-record arrays, 10,000/100,000 integers, floats and large
escaped/raw Unicode text, each as str and bytes. The 100,000-integer cases
improved 2.08–2.10×; all other controls measured 0.98–1.02×. Its separate
[raw control report](json-decode-scale-controls.json) also passed the 15%
slowdown gate. All 36 measured workloads passed their gates.

Table latencies are medians of all 15 retained wall-clock batch means per
revision. Speedup is the reciprocal of the median adjacent after/before ratio;
it can differ slightly from dividing the independent latency medians. The raw
report also retains the inherited harness's separate fastest-round summaries;
those are not the table's latency estimator.

## Proof and measurement

- The [raw scale report](json-decode-scale-results.json) retains every adjacent
  batch, calibrated iteration counts, stdlib controls, output checks, fixture
  hashes, environment and loaded source/binary hashes.
- The [driver](../../profiles/json_decode_scale.py) uses the existing
  [throughput harness](../../profiles/jsonx_throughput.py). Three fresh workers
  retain both revisions and alternate before/after order over five rounds.
  Every call allocates its complete result; fixture construction and validation
  happen outside timing. Validation compares types, float bits and dictionary
  key order against stdlib JSON.
- The [source manifest](json-decode-scale-source-manifest.json) and
  [source archive](json-decode-scale-sources.tar.gz) preserve both trees. Python
  sources are identical between measured revisions; only `decode.h` and the
  rebuilt native binary differ. Both were compiled with identical flags.
  The baseline is the pre-task working tree, including existing uncommitted
  work. This isolates the native change from concurrent streaming-reader edits.
- The current workspace's [serde test run](json-decode-scale-tests.txt) passed
  **4,336 tests**, with 24 skipped and five resource tests excluded by the
  repository's default selection. Ruff and mypy passed for the new benchmark.
  The [new parity tests](../../../tests/suites/product/serde/parity/test_jsonx_single_parse.py)
  add 138 cases, including observable single-parse probes above the old limit,
  late numeric precision, Unicode, malformed input, full stdlib error metadata
  and duplicate-key hook histories. Existing depth and source-limit tests pass.
- The current workspace's [native acceleration qualification](json-decode-scale-acceleration.json)
  verifies live public bindings, exact outputs and fallback behavior separately
  from timing.

## Reproduce

Use the repository's development environment with the serde extras installed.
The archive includes the measured macOS ARM64 CPython 3.14 binaries and a local
rebuild script. Extract it into a fresh directory and rebuild for your runtime:

```sh
mkdir /tmp/json-decode-scale
tar -xzf bench/reports/serde/json-decode-scale-sources.tar.gz -C /tmp/json-decode-scale
.venv/bin/python /tmp/json-decode-scale/rebuild.py
.venv/bin/python -m bench.profiles.json_decode_scale \
  --compare-source /tmp/json-decode-scale/before/src \
  --source /tmp/json-decode-scale/after/src --require-native \
  --rounds 5 --repetitions 3 --max-slowdown 1.15 \
  --output /tmp/json-decode-scale/results.json
.venv/bin/python -m bench.profiles.jsonx_throughput \
  --compare-source /tmp/json-decode-scale/before/src \
  --source /tmp/json-decode-scale/after/src --require-native --include loads/ \
  --rounds 5 --repetitions 3 --max-slowdown 1.15 \
  --output /tmp/json-decode-scale/controls.json
.venv/bin/python -m pytest tests/suites/product/serde -q
```

The native `loads` dispatch is specific to CPython 3.14. Other interpreter and
platform performance is not established by these measurements.
