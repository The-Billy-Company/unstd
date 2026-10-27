---
doc_radar:
  sentinels:
    - file: reproduce.py
      contains: ['shared-physical-path-v2', 'raw_call_samples_ns', 'time.process_time_ns', 'published.unlink()', 'path.parent.rmdir()', 'independent_path_objects=not args.shared_path_object']
    - file: directories.py
      contains: ['itertools.permutations(operations)', 'cached_path', 'before-fs.py.gz']
    - file: ../../../../src/unstd/fs/__init__.py
      contains: ['check_first: bool = True', 'p = Path(path)', 'if not check_first or not p.is_dir():']
      absent: ['_cached_path_text', '_PATH_STRING_CACHE']
---

# Filesystem Path Measurements

We measure complete record and sidecar writes against their stdlib forms. The
shared forms use `ensure_dir`; record publication also uses `atomic_replace`.

The minimal directory-policy change is the only accepted production change.
The 4-8% write-regression gate remains open, and the 100x target was not met.

- [Reproduce](#reproduce)
- [Method](#method)
- [Results](#results)
- [Evidence](#evidence)

## Reproduce

Run these independent synthetic shapes with an installed `unstandard` package.
Application source stays outside this repository; the public harness uses its
own payloads and writer definitions.

```sh
python bench/reports/fs/path-cache/reproduce.py --rounds 18 --loops 120
python bench/reports/fs/path-cache/reproduce.py --fresh --rounds 11 --loops 120
python bench/reports/fs/path-cache/reproduce.py --control --rounds 11 --loops 120
python bench/reports/fs/path-cache/directories.py
```

The writer harness checks output bytes, permissions, returned paths, error
details and symlink cases before measuring. `before-fs.py.gz` retains the
released public module; selected sources use the installed durability and
traversal helpers.

Use the [package README](../../../../README.md) for installation and project
support, or the [filesystem README](../../../../src/unstd/fs/README.md) for the
operation contracts.

## Method

We interleave every permutation of execution order and retain wall and process
CPU separately. CPU profiles run afterward; their instrumentation cost makes
them call-graph evidence rather than latency measurements.

Each arm receives an independent Path object for the same physical pathname.
The explicit `--shared-path-object` control recovers the earlier shared-object
method, where one arm could warm another arm's lexical cache.

`--fresh` starts every invocation with its parent absent. We remove the
published file and parent after stopping the clock, then assert that the parent
is gone; existing-parent runs repeatedly publish to the same target.

`--control` compares the identical stdlib function with itself. Reports retain
per-call samples and each round's median; small differences inside the A/A
spread do not earn a speedup claim.

The first fresh-parent experiments used different names for each arm. We
preserve those results, then repeat with the shared physical path to remove
name and metadata-cache bias without changing the required writes.

The first input-shape directory experiment used rotation plus reversal, which
did not fully balance positions. `guarded-directories.json` replaces that
acceptance method with all permutations and independent inputs; the old
`directory-inputs.json` remains diagnostic evidence.

## Results

The lexical-cache experiment lost performance in the corrected real-consumer
repeat. Guarded False-mode calls cost 6.3-6.8% more wall time with existing
parents and 8.4-9.3% more with missing parents; process CPU also regressed.

The original-normalization False-mode control still lost 2.6-4.2% wall time
with existing parents and 5.2-6.6% with missing parents in that repeat. CPU
changes ranged from 0.6% to 5.2%; every raw sample remains available, including
the earlier noisy and favorable runs.

We rejected each shortcut that changed behavior:

- **Path reuse** - returning the source Path shares mutable `Path.info` caches
  on Python 3.14 and loses the old fresh-object behavior.
- **Unconditional mkdir** - changing the default emits a creation audit event
  for existing directories, where the released default succeeds without one.
- **Lexical-cache transfer** - an exact Path may contain valid raw str
  subclasses with changing parsing hooks, so its cached text can differ from a
  fresh `Path(source)` without any Path binding changing.
- **Native directory query** - extra binding checks failed to earn a material
  win across the public input forms; unsafe variants remain rejected.

The retained `check_first=False` API makes the caller's creation attempt and
audit policy explicit while the default keeps its released policy. It retains
normal Path construction and independent metadata caches; this behavior fix
alone does not prove performance parity.

We also rejected an optimistic native fresh-allocation prototype after the
whole workflows failed to close the regression. Record writes still cost
5.6% more wall time with existing parents and 4.0% more with missing parents;
the latter sidecar case cost 3.9% more.

That prototype saved about 87ns for raw Path construction and 144ns for
construction plus string conversion in a separate CPU microbenchmark. It kept
raw lists independent and caches unset, but before-import constructor
admission, allocation/GC reentrancy and broader interpreter qualification
remain unresolved; those numbers do not qualify a production fast path.

An optimistic compiled-C reference still performs real open, write, close and
rename operations with preencoded bytes and prevalidated names. Its observed
wall gains were 1.14-1.35x, not 100x; it omits hooks, audits and full error
handling, so it is an opportunity reference rather than a compatible
replacement or universal lower bound.

## Evidence

We pin sources with hashes and preserve every measured gain and loss in JSON.
These are local filesystem measurements; they do not establish a fleet-wide
speedup.

- **Consumer reports** - `consumer-results.json` contains round summaries;
  `consumer-results.json.gz` also retains raw paired calls.
- **Directory reports** - `guarded-directories.json` contains corrected input
  controls; its gzip companion retains raw paired calls.
- **Source captures** - compressed public filesystem modules identify the
  released, rejected and minimal-policy variants without application source.
- **Native references** - `os-floor.c` and `os-floor.json` retain the optimistic
  syscall comparison; `native-query.c` records the rejected query experiment.
- **Raw allocation** - `native-raw-clone.c`, its compressed filesystem adapter
  and `native-normalization.json` retain the rejected optimistic clone proof.
  `build-normalization.py` builds it locally; place this folder on `PYTHONPATH`
  when selecting `native-raw-clone-fs.py.gz` with the writer harness.
