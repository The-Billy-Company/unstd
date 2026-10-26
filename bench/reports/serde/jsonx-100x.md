# JSON shared-branch acceleration

Qualified on 2026-10-25 with the existing `jsonx.dumps`, `dumpb` and `loads`
APIs. The 100× target is achieved for named shared-row byte-encoding cases.
It is not a uniform claim for arbitrary independent JSON values.

## Primary Foundry results

Two persistent workers alternate revisions for each case, reverse case order
across repetitions, and retain raw wall/CPU samples and timestamps. These table
entries are the reciprocal of the median matched after/before wall-time ratios.
They use no stdlib-control normalization. The prework column measures this turn's
change; the historical column uses commit
`94589856d2e604310b55020e40c51010f2af4755`. Both sides use the same CPython 3.14.9
interpreter, orjson 3.12.0, msgspec 0.22.0 and default production allocator on
Billy's Foundry (Linux x86_64, Ryzen 9 7950X3D).

| Public call and input | Versus immediate prework | Versus historical original |
|---|---:|---:|
| Compact bytes, repeated fixed-float row | 25.1× | 494.2× |
| Compact bytes, repeated varied-float row | 127.6× | 143.2× |
| Default text, repeated fixed-float row | 11.7× | 210.1× |
| Default text, repeated varied-float row | 85.4× | 95.3× |

The varied-float input is explicit structural sharing:

```python
row = [index / 7 for index in range(128)]
value = [row] * 256
raw = jsonx.dumpb(value)
```

The fixed-float case uses a 256-element row of `1.5`, repeated 256 times.
Equal-valued independent controls use `[list(row) for _ in range(256)]`.
Both forms emit identical JSON bytes. Only the shared form can reuse a
previously validated branch. Every call allocates its complete output;
mutations between calls remain visible, and no encoded result survives a call.

The original 50 workloads retain their names and inputs; eight new cases label
shared/independent structure separately. Independent varied-float encoding is
near its previous speed. The independent fixed-float text control measured 8.2%
slower in the closer paired run; the whole-worker medians measured 17.9% slower.
Both results and their controls are retained.
Wide ASCII/Unicode record
**decoding** benefits separately from eliminating a redundant parse, with
roughly 2× gains. The complete 58-case comparisons, controls, and measured losses
are retained below. macOS arm64 separately measures about 140× for shared varied-float
byte encoding versus prework; its full comparison uses the frozen whole-worker
protocol rather than the targeted Foundry case-interleaved protocol.

## Implementation and boundaries

The bounded native writer stores up to 32 completed flat-container spans,
retaining owned inputs during one uninterrupted GIL span. Reuse requires the
same object and recursion depth, and every repeated logical value still spends
the 262,144-visit budget. Output remains private and capped at 1 MiB; unsupported
inputs return to the existing authoritative fallback. Memo admission requires
at least 32 members in the shared branch and 4,096 estimated flat visits, which
keeps tiny inputs on their faster existing paths. Safe ASCII spans of at least
eight characters use bulk copies; short keys retain the previous character
writer. A compiler-inline correction keeps scalar calls off an extra frame.

The decoder result proof now finishes each child before its sibling, bounded by
64 container frames and the existing 65,536-value budget. A shallow list of more
than 64 records no longer fills the old sibling queue and discards a successful
parse. Floats, large integers, discarded-source limits, deep refusals and
stdlib diagnostics retain their precise fallback behavior.

## Validation and packaging

- Foundry native wheel and independently rebuilt source-distribution wheel:
  **3,191 passed**, 25 expected skips and five performance deselections each.
- macOS pristine native-wheel consumer: **3,192 passed**, 24 expected skips and
  five performance deselections.
- Isolated ASan/UBSan builds on CPython 3.12.15, 3.13.17 and 3.14.9, each with
  malloc and debug allocators: **1,184 passes**, four expected skips, all six
  widened alias-stress runs passed, and no sanitizer/allocator findings.
- Stress covers large shared matrices, mixed depths, ownership/refcounts,
  mutation between calls, cycles, replacement, output limits and logical budget
  boundaries. Full Serde contracts retain formatting, hooks, errors and source
  numeric/recursion limits.

The two Foundry build routes contain identical JSON machine-code sections and
Python source bytes, with a complete native C/header source closure. The final
58-case paired packaging comparison's largest slowdown is 9.4%.
The shared host was busy; raw unpaired latency medians sometimes compare
different clock/load phases. Those raw verdicts remain intact. The paired
summary groups the already-declared case/repetition blocks without normalizing
to stdlib controls. Whole-worker results are context, not adjacent batch pairs.

Preliminary source-specific data is retained, including the detected tiny-alias
and short-key regressions. The final admission gate and eight-character ASCII
threshold fix those measured regressions; they do not change benchmark inputs.
The final source hash for `repeat.h` is `15a7ba8b7c3d07646a4d4ad0d5770f514ca21142d70162e9c1f18959b180d663`.

CI now measures all eight shared/independent controls in addition to the ordinary
record cases, requiring native dispatch and retaining evidence before a 15%
slowdown failure. The current adjacent-batch driver caps estimated slow-batch
work so a large speedup cannot accidentally create multi-second baseline batches.
The measurements here retain their exact frozen drivers and protocols.

## Current package layout

The primary measurements above preserve the source layout in their receipts.
The subsequent helper rename leaves the JSON function/class bodies unchanged;
the moved helper bodies differ only by empty `__all__` markers. The native
C/header sources are unchanged. The current workspace passes its native
preflight and **3,262 tests**, with 24 expected skips and five performance
deselections, covering the full Serde suite and focused JSON benchmark,
release and workflow gates. The source archives retain both layouts.

Fresh strict wheel and source-distribution builds of this layout each pass
**3,191 tests**, with 25 expected skips and five performance deselections.
The eight-case Foundry follow-up measures **129.5×** for shared varied-float byte
encoding using the same matched estimator and prework baseline. Its independent
fixed-float text control is 9.1% slower; the remaining independent controls are
within 1.1% slower to 5.4% faster. The full primary table remains tied to its
original frozen source receipt.

## Reproducible evidence

- [Foundry source/build/test/ELF and allocator receipt](jsonx-100x-foundry-qualification.json)
- [Final shared cases versus immediate prework](jsonx-100x-foundry-final-shared-prework-py314.json)
- [Final shared cases versus historical original](jsonx-100x-foundry-final-shared-original-py314.json)
- [Final 58-case prework comparison](jsonx-100x-foundry-prework-final-py314.json)
- [Final 58-case wheel/source packaging comparison](jsonx-100x-foundry-final-packaging-py314.json)
- [Paired aggregation and retained raw ratios](jsonx-100x-foundry-paired-summary-py314.json)
- [Scalar-call controls](jsonx-100x-foundry-final-scalars-py314.json)
- [Prework JSON source archive](jsonx-100x-prework-source.tar.gz)
- [Final JSON source archive](jsonx-100x-release-source.tar.gz)
- [Source archive for the measured package layout](jsonx-100x-pre-layout-release-source.tar.gz)
- [Package layout compatibility comparison](jsonx-100x-layout-compatibility.json)
- [Current workspace native and test receipt](jsonx-100x-workspace-qualification.json)
- [Current-layout Foundry wheel/source/test receipt](jsonx-100x-foundry-live-qualification.json)
- [Current-layout Foundry build inputs and logs](jsonx-100x-foundry-live-build-logs.tar.gz)
- [Current-layout Foundry shared and independent follow-up](jsonx-100x-foundry-live-shared-prework-py314.json)
- [macOS build/test/source receipt](jsonx-100x-macos-qualification.json)
- [macOS build inputs and raw logs](jsonx-100x-macos-build-logs.tar.gz)
- [macOS final 58-case comparison](jsonx-100x-macos-prework-release-py314.json)
- [macOS final shared cases versus original](jsonx-100x-macos-original-release-py314.json)
- [Preliminary macOS wide-float adjacent-batch check](jsonx-100x-macos-wide-floats-paired-py314.json)
- [Final short-key GCC check](jsonx-100x-foundry-ascii-min8-wide-py314.json)
- [Frozen 58-case harness source](jsonx-100x-foundry-harness.py.txt)
- [Primary case-interleaved driver](jsonx-server-foundry-interleaved.py.txt)
- [Frozen harness and source snapshots](jsonx-100x-source-snapshots.tar.gz)
- [ASan/UBSan final receipt](jsonx-100x-safety.json)
- [ASan/UBSan raw logs and XML](jsonx-100x-safety-logs.tar.gz)
