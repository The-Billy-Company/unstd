# Edit distance for long, near-identical strings

The base-install `fuzz.distance` now removes identical, nonoverlapping prefixes
and suffixes before computing uniform Levenshtein distance. The C and Rust
kernels borrow the remaining Unicode storage; the Python fallback trims before
allocating bit masks or selecting its matrix fallback. Equal ends contribute
no edits, so results remain exact.

This fixes a performance cliff: long strings with a tiny edit previously
exceeded the native length/alphabet/work limits and fell back to large Python
integer operations or a cell-by-cell matrix. The native limits now apply to the
remaining middle. A separate limit of 4,194,304 attempted affix comparisons,
including mismatches at both ends, keeps native scans bounded. Unsupported
residuals still defer to Python. Arbitrary sequences and string subclasses keep
their previous dispatch and callbacks.

## Measurements

October 27, 2026, macOS arm64, CPython 3.14.9. These are synthetic workload
shapes on one machine, without RapidFuzz. The installed RapidFuzz public binding
is unchanged. See [raw evidence](affixes-results.json) for every sample, observed
dispatch, source/binary hashes, interpreter configuration and allocation peaks.

| Base path and workload | Before | After | Paired speedup |
| --- | ---: | ---: | ---: |
| Native available, 65,536 shared prefix + `cat` / `cats` | 1.354 s | 45.6 µs | 29,772× |
| Native available, 32,768 shared suffix, one insertion | 380.8 ms | 22.9 µs | 16,349× |
| Native available, 4,096 shared characters at each end, one substitution | 1.227 ms | 5.94 µs | 208× |
| Native available, mixed Unicode storage, 32,768 shared prefix | 346.1 ms | 20.0 µs | 17,768× |
| Native available, 300 distinct shared Unicode characters at each end | 19.33 ms | 0.447 µs | 42,927× |
| Source-only, 65,536 shared prefix + `cat` / `cats` | 1.431 s | 1.078 ms | 1,340× |
| Source-only, shared ends totaling 8,192 characters | 28.21 ms | 195.8 µs | 144× |

Every shared-end workload has distance **1**. Unequal strings provide a lower
bound of one edit; the explicit single insertion/substitution constructs the
other string, proving the matching upper bound independently of trimming.

Controls remain visible: native `kitten` / `sitting` measured 115 → 116 ns;
unrelated 400-character inputs measured 5.28 → 5.67 µs (about 7% slower).
Source-only controls measured 1.63 → 1.82 µs for the short pair (about 12%
slower), and 159.9 → 156.5 µs for unrelated 400-character strings. Empty-input
shortcuts were unchanged. The added scan has a small constant cost; this is a
large improvement for shared-end workloads, not a universal speedup.

For the 65,536-character prefix, peak additional Python allocations measured
973,328 → 0 bytes with the native kernel available and 973,328 → 192 bytes with
native dispatch disabled. Inputs were prepared before tracing; these are
`tracemalloc` peaks, excluding the interpreter, input strings and C stack
scratch. The result `1` uses Python's existing small-integer object.

The [driver](../../profiles/text_affixes.py) loads the original and candidate
wrappers separately with their own explicit native binaries. Both use the base
public binding, and the source-only lane disables native dispatch. Untimed
instrumentation proves native acceptance/fallback before timing. In the large
prefix case, the original actually declines into Python; the candidate completes
in C. Seven rounds alternate before/after order, with each arm independently
calibrated to a batch of at least 5 ms. Every wall/CPU batch is retained, with
GC enabled. Latencies are medians; speedups are medians of matched ratios.
Checks, calibration and allocation tracing are outside measured batches.

## Validation

The complete text suite passes **416 tests** with the rebuilt C extension and
**416 tests** with the separately built Rust binding.
Tests include an independent, untrimmed edit-cost matrix; exhaustive short
strings; generated Unicode; combining marks, NULs and lone surrogates; mixed
1/2/4-byte Unicode representations; prefix/suffix overlap; and residual length,
alphabet and work-limit refusals. Exact and over-limit affix scan cases cover
the combined prefix/suffix budget. A fresh subprocess blocks both RapidFuzz and
the native extension and exercises the public source-only API.

The Rust core passes all 10 debug/release tests, formatting and Clippy. Separately
compiled C/Rust bindings also matched an independent matrix on 2,500 seeded
Unicode/affix pairs in both argument orders, plus five large admission cases.
Ruff and strict mypy pass on the changed Python implementation/driver.

A broader product run passed **10,847 tests**, with 220 capability/platform
skips and 5 opt-in deselections. It excluded `tests/suites/product/cache`: an
unrelated feature was being edited concurrently in this checkout, and its
incomplete `Cache` import blocked the initial full-product collection. Those
files were left untouched. This is not a claim that the entire concurrently
changing checkout passed every gate.

## Reproduce

From the repository root, with the development environment installed, this
builds both text extensions with the same setuptools procedure. The original
source is pinned to the pre-change Git commit. Build output stays in `.local`.

```python
# Run with: .venv/bin/python
from pathlib import Path
import subprocess
import sys
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext

root = Path.cwd()
before = root / ".local/text-affixes/repro-before"
revision = "0b00d72a6e5155322e06c4594c7b829e5fd47788"
for relative in ("src/unstd/text/fuzz.py", "native/text/src/adapter.c"):
    target = before / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        subprocess.check_output(["git", "show", f"{revision}:{relative}"])
    )

binaries = {}
for label, source_root in (("before", before), ("after", root)):
    extension = Extension(
        "unstd.text._text_native",
        [str(source_root / "native/text/src/adapter.c")],
        extra_compile_args=["-fno-fast-math", "-ffp-contract=off"],
    )
    command = build_ext(Distribution({"ext_modules": [extension]}))
    output = root / ".local/text-affixes" / f"repro-{label}-build"
    command.build_lib = str(output / "lib")
    command.build_temp = str(output / "temp")
    command.ensure_finalized()
    command.run()
    binaries[label] = command.get_ext_fullpath(extension.name)

subprocess.run(
    [
        sys.executable,
        "bench/profiles/text_affixes.py",
        "--baseline-source",
        str(before / "src"),
        "--baseline-native",
        binaries["before"],
        "--candidate-source",
        str(root / "src"),
        "--candidate-native",
        binaries["after"],
        "--rounds",
        "7",
        "--output",
        ".local/text-affixes/reproduced.json",
    ],
    check=True,
)
```

The strict floating-point compiler arguments above match the macOS/Linux text
build. On MSVC use `/fp:strict`. Rebuild the editable native extension before
running the C product suite:

```console
uv sync --locked --all-extras --group dev --group release --group build --reinstall-package unstd
uv run --no-sync pytest tests/suites/product/text -q
cargo test --manifest-path native/text/rust/Cargo.toml --locked --offline
```
