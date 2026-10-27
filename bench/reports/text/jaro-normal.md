# Ordinary Jaro matching on Linux and macOS

Ordinary contact lookup, top-five matching and all-pairs scoring now use
RapidFuzz's native batch kernels when the caller selects
`scorer=fuzz.jaro_winkler`. Previously, those helpers called the scalar scorer
once per pair in Python even when the `text` extra was installed. This change
removes that dispatch and ranking overhead on both Linux and macOS.

The route activates automatically for exact strings in ordinary lists or tuples,
with the normal numeric cutoff and limit arguments. `best_match` and `extract`
need `unstd[text]`; `cdist` additionally needs NumPy. Scalar scores, inclusive
cutoffs, duplicate handling and stable ties are preserved. General iterables,
mappings, subclasses, custom scorers, weighted partials and custom numeric hooks
retain their prior execution behavior. Base installs keep their bundled native
and portable implementations, including the earlier [linear fallback](jaro.md).

```python
from unstd.text import fuzz

contacts = ["John Smith", "Joan Smith", "Jonathan Smyth"]
fuzz.best_match("Jon Smth", contacts, scorer=fuzz.jaro_winkler)
fuzz.extract("Jon Smth", contacts, scorer=fuzz.jaro_winkler, limit=2)
fuzz.cdist(["Jon Smth", "Joan Smit"], contacts, scorer=fuzz.jaro_winkler)
```

## Measured normal workloads

October 27, 2026. The fixtures contain **624 short contact names**, formed from
24 given names and 26 surnames, plus 24 ordinary deletion-typo queries. The
matching cutoff is 0.75. These measurements include complete result construction,
cutoff filtering and sorting/selection; correctness and setup are outside timing.
Matrices use one worker and return Python lists of Python floats.

| Platform | Public operation | Before | After | Paired speedup |
| --- | --- | ---: | ---: | ---: |
| macOS ARM64 | Best contact match | 128.2 µs | 39.60 µs | **3.18×** |
| macOS ARM64 | Top five contacts | 136.5 µs | 64.73 µs | **2.63×** |
| macOS ARM64 | 24 × 624 contact matrix | 3.889 ms | 1.102 ms | **3.30×** |
| Linux ARM64 | Best contact match | 116.7 µs | 40.93 µs | **2.86×** |
| Linux ARM64 | Top five contacts | 151.7 µs | 60.08 µs | **2.69×** |
| Linux ARM64 | 24 × 624 contact matrix | 3.571 ms | 1.135 ms | **3.12×** |

A second fixture uses **77 fixed repository module paths** and 24 deletion
queries, rather than discovering whichever files happen to exist during a run.
Best match / top five / matrix speedups are **2.46× / 2.06× / 2.61× on macOS**
and **2.64× / 2.27× / 2.07× on Linux**.

Raw evidence: [macOS](jaro-normal-macos.json), [Linux](jaro-normal-linux.json),
and [Linux environment/build receipts](jaro-linux-environment.json). Every
wall-clock and process-CPU sample, arm order, batch count, output hash,
source/binary hash and observed dispatch route is retained. Reported latencies
are separate medians; speedups are medians of matched before/after ratios,
so dividing the displayed medians can give a different ratio.

## Proof of execution and compatibility

The benchmark loads the saved and candidate public wrappers separately. It
requires actual compiled RapidFuzz scalar and process binaries, recording their
paths and SHA-256 hashes. Instrumentation outside timing observes the candidate
entering `process.extractOne`, `process.extract` and `process.cdist` with the
selected built-in scorer. The baseline does not enter those batch APIs. The
driver fails and saves its report if any candidate batch route is missing or
RapidFuzz silently selects its Python implementation.

The base-install control also observes native execution: the 24 × 624 matrix
makes **14,976 accepted native Jaro calls, with zero declines**, in each arm on
both platforms. Its source algorithm is unchanged by this follow-up. Base
controls show paired speedups of 0.79–1.07× on macOS and approximately 0.96–1.11×
on Linux, all within the retained 30% wall-time regression ceiling. The macOS
path-lookup wall medians were 74.7 → 103.1 µs while CPU medians were 72.4 →
75.1 µs; raw wall variation remains visible rather than being replaced by CPU
time. Scalar RapidFuzz calls continue to use their original function directly.

For every workload, the complete output must exactly match an exhaustive scalar
ranking or matrix before and after timing. Both platforms used identical source
and fixture hashes; **all 16 complete output hashes agree across Linux and
macOS**, including the base controls. Separate boundary checks cover ties,
duplicates, exact and next-float cutoffs, NaN, empty results and empty matrices.

Validation passes **532 focused tests on macOS**: 506 text contracts, the public
text signature contract, and 25 workflow/harness contracts. The same **506 text
contracts pass on Linux**, with no skips. Regressions preserve generator
exhaustion and exceptions after perfect matches, None/NaN inputs, subclass hooks,
custom cutoff comparison order, custom slice-index timing, weighted partials,
NumPy-free matrices, worker forwarding, and exact float64 cells. Scoped Ruff,
formatting and strict mypy checks pass.

## Environment and limits

macOS uses CPython 3.14.9/Clang on ARM64. Linux uses CPython 3.14.4/GCC 15.2 on
ARM64 with glibc 2.43, inside a Docker Desktop Linux VM on the same Mac. Linux
loads real Linux RapidFuzz and NumPy wheels and a separately rebuilt C text
extension; it does not execute macOS binaries or emulate Linux with patched
platform variables. Both use RapidFuzz 3.14.6 and NumPy 2.5.3.

The two benchmarks ran sequentially. Seven rounds alternate arm order, using
independent calibration to batches of at least 5 ms, with garbage collection
enabled. Source/binary receipts are rechecked after timing. Each run enforces
at least **2× improvement** for ordinary contact batch operations, and at most
**1.30× wall latency** for all controls; both platforms pass. These are
representative synthetic names and real module-path strings, not an application
latency forecast. Linux VM measurements do not establish bare-metal Linux,
Linux x86-64, Windows or every deployment's absolute performance.

The [performance workflow](../../../.github/workflows/performance.yml) now
runs this comparison in its existing **Ubuntu/macOS × Python 3.13/3.14** matrix
against each revision's separately installed product and native binary. Future
unchanged revisions can pass: the recurring contact guard requires at least
0.80× baseline speed, and the overall wall-time ceiling remains 1.30×. Candidate
native batch dispatch is always required, and the existing always-uploaded
artifact retains `text-jaro-normal.json` even when a check fails. This workflow
was validated locally; its new remote jobs have not been run in this chat.

## Reproduce

The [source archive](jaro-normal-sources.tar.gz) contains the exact before/after
wrappers, C text source, shared harness and a SHA-256 manifest. The baseline
already contains the linear fallback improvement, so these timings isolate the
ordinary batch-dispatch change. Baseline SHA-256:
`5127d7a5aa914e8e45d8a95e7a9bbcf2584ab97168d641cd46b729ac7827aae4`;
candidate SHA-256:
`becb6f8adf74043e4d11d400391a43d83c97fe0d7fee121b76f1305d561e017b`.

With the development environment and a built text extension, extract the saved
sources and run the current driver:

```sh
mkdir -p .local/jaro-normal-repro
tar -xzf bench/reports/text/jaro-normal-sources.tar.gz -C .local/jaro-normal-repro
JARO_NATIVE=$(.venv/bin/python -c 'import unstd.text._text_native as n; print(n.__file__)')
.venv/bin/python bench/profiles/text_jaro_normal.py \
  --baseline-source .local/jaro-normal-repro/before \
  --candidate-source .local/jaro-normal-repro/after \
  --baseline-native "$JARO_NATIVE" --candidate-native "$JARO_NATIVE" \
  --rounds 7 --minimum-speedup 2 --max-slowdown 1.30 \
  --output .local/jaro-normal-repro/results.json
.venv/bin/python -m pytest tests/suites/product/text -q
```

The same command runs on Linux with its own built extension and installed Linux
dependencies. Runtime and native-binary identities are recorded per platform.
