# JSON acceleration on Foundry

Validated on 2026-10-25 with fresh CPython 3.14.9 builds on Billy's Foundry
(AMD Ryzen 9 7950X3D, Linux x86_64) and macOS arm64. The native JSON paths survive
both the direct wheel and a wheel independently rebuilt from the source
distribution. Foundry's two clean builds contain identical JSON machine-code
`.text` and `.rodata` sections and identical Serde Python source bytes.

Both Foundry build routes pass all seven installed-native qualification groups
and **3,159 Serde checks**, with 25 version/optional skips and five deliberate
performance deselections per route. The source distribution contains the full
C/header closure, including the float, ASCII and identity helpers. The build
receipt retains all source hashes. Every JSON source/header and the dependency
lock match this working tree. Concurrent later changes append an
unrelated container extension to the build hook and wheel test configuration;
they leave JSON's build inputs and flags intact.

## Gains retained on Linux

These ratios compare the current implementation with commit
`94589856d2e604310b55020e40c51010f2af4755` independently on each host. Larger is
faster. Both use orjson 3.12.0 and msgspec 0.22.0, with identical dependencies
within each comparison; absolute wall times across CPUs are separate facts.

| Operation, 1,000 records | macOS gain | Foundry gain |
|---|---:|---:|
| Default record encoding | 1.49× | 1.68× |
| Compact record bytes | 1.72× | 1.92× |
| Unicode record encoding | 1.79× | 1.83× |
| Two-space ASCII records | 3.04× | 3.21× |
| Strict identity snapshots | 1.53× | 1.62× |

The clean locked Foundry comparison measures 21 record cases; every case stays
within the 15% wall-time limit, including byte/string decoding. Raw wall and
thread-CPU batches, stdlib controls, source/binary hashes, compiler settings and
active CPython 3.14 bindings are retained. The macOS comparison uses seven timing
rounds and three alternating repetitions; the final Foundry comparisons use
nine rounds and five repetitions.

## Packaging and shared-host timing

Native code, Python bytes, source closure, actual kernel admission and full
contracts establish that source packaging retains the implementation. Foundry
was shared with active CI work (load around 28–33 on 32 threads); the qualification
container used CPU 30, one CPU of quota and 2 GiB of memory.

The initial latest-backend direct/rebuilt comparison passed all 21 cases with
a worst slowdown of 6.5%. The locked-version whole-worker comparisons failed
six cases above 15%, despite identical generated JSON code. Those failures are
retained. Control timings also moved substantially; this is evidence of timing
instability, and does not establish a consistent package-induced slowdown.
To reduce the interval between the two builds, a diagnostic kept two installed
workers alive and alternated them for each case, reversing case order across
repetitions. It reused the unchanged fixtures, correctness oracles and raw
wall/CPU timer, with no control normalization. All 21 cases passed the 15% limit;
the largest measured slowdown was 9.94%, and float-record byte encoding differed
by 1.42%. The exact diagnostic source and its hash are retained. Together with
identical machine code and Python bytes, this supports retention of packaging
performance within the measured variation. It does not promise identical
elapsed times under every shared-host load.

The initial historical/latest-backend comparison also measured a 20% byte-decode
loss; the longer 20-case decoding repeat measured that case 4% faster. No source
change was made between those runs.

## Ongoing checks

Every release wheel now runs `tests/tooling/runtime/json_acceleration.py` before
its full installed-wheel contracts. It rejects missing native modules/backends,
detached public bindings, inactive CPython 3.14 dispatch, and declined native
float, ASCII, identity or NDJSON probes even if fallback output is correct.
Performance CI additionally compares installed record workloads on Linux/macOS
with `--require-native`, retaining evidence before rejecting a slowdown above
15%. The final focused JSON/native/harness/workflow run passed **64 checks**;
Ruff and the targeted benchmark type check passed.

An earlier broader gate selection passed 114 checks. After concurrent container
extension changes, its final rerun passed 113 and failed three: two wheel-runner
fixtures still assume five product modules while the runner now includes containers,
and the release test-dependency export lacks the newly declared containers
extra. These in-progress changes leave the broader release gate failing; they
are independent of the unchanged JSON source and the successful Foundry
qualification. Their failing test names are:

- `test_real_workers_all_start_keep_cwd_and_preserve_failures[failures0]`
- `test_real_workers_all_start_keep_cwd_and_preserve_failures[failures1]`
- `test_real_offline_wheel_export_keeps_every_transitive_locked_hash[tests]`

## Evidence

- [Foundry build, source, ELF, native-check and test receipt](jsonx-server-foundry-qualification.json)
- [Clean locked original/current record timings](jsonx-server-foundry-py314-locked.json)
- [Clean locked direct/rebuilt whole-worker timings, including failures](jsonx-server-foundry-packaging-py314-locked.json)
- [Per-case alternating locked packaging timings](jsonx-server-foundry-packaging-interleaved-py314-locked.json)
- [Exact diagnostic driver source](jsonx-server-foundry-interleaved.py.txt)
- [Initial locked direct/rebuilt timing failure](jsonx-server-foundry-packaging-py314-locked-initial.json)
- [Latest-backend full 50-case comparison](jsonx-server-foundry-py314.json)
- [Latest-backend 20-case decoding repeat](jsonx-server-foundry-loads-py314.json)
- [Latest-backend direct/rebuilt comparison](jsonx-server-foundry-packaging-py314.json)
- [macOS installed-wheel receipt](jsonx-server-macos-qualification.json)
- [macOS original/current record timings](jsonx-server-macos-py314.json)
- [macOS installed-native qualification](jsonx-server-macos-native.json)
- [macOS installed-wheel Serde tests](jsonx-server-macos-tests.txt)
