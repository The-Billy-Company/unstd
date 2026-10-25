---
doc_radar:
  sentinels:
    - file: bench/profiles/iters_backend.py
      contains: ['def _paired(', '"source_hashes": hashes', '"windowed-stride64/8192"']
    - file: src/unstd/iters/consecutive.py
      contains: ['previous, view = tee(view)', 'window.extend(islice(it, step - 1))']
---

# Iterator backend evaluation

We keep the iterator backend in the stdlib. The useful changes remove Python
work and quadratic window setup; the public API needs no additions.

[`backend-macos-py314.json`](backend-macos-py314.json) retains 27 output-checked
workloads, nine alternating adjacent timing pairs, raw wall and thread-CPU
samples, interpreter details, and hashes of both source trees and the harness.
These are local macOS arm64 / CPython 3.14 measurements, not fleet guarantees.

| Workload | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| First window, width 4096 | 18.31 ms | 342.33 us | 53.48x |
| Four-item windows, stride 64, 8192 items | 135.69 us | 55.61 us | 2.44x |
| Adjacent two-item windows, 64 items | 1.53 us | 1.04 us | 1.47x |
| Eight-item chunks, 8192 items | 37.99 us | 34.82 us | 1.09x |
| Keyed deduplication, 8192 items | 320.88 us | 293.63 us | 1.09x |

The baseline iterator files came from commit
`a4634b122cded4299cbe6cb30885e305241db3f3`. Other packages had uncommitted work;
the profile loads only the selected iterator modules under private aliases.
It compiles the recorded source bytes rather than trusting cached bytecode.

The first wide window now needs O(n) setup: each next view forks the current
position, instead of walking offsets 0 through n-1 separately. Subsequent
windows still use C-level `zip`. Size-two windows use `pairwise`. Strided windows
advance all but the final item in `deque.extend`; the final pull proves that a
complete stride was read. Custom or oversized strides retain the previous
validation path. Keyed deduplication stays on the set-only path until an
unhashable key requires mixed-history comparisons.

The focused contracts cover delayed errors, custom index providers, oversized
sizes, exact reads, mixed hashability, and retention across gaps. The combined
product and profile checks pass 162 tests; Ruff and mypy also pass. Broader
registry checks encountered unrelated concurrent package restructuring and do
not establish a clean repository-wide gate.
Compatibility grids also pass on CPython 3.12.15 and 3.13.17: 1620 window
combinations per interpreter, strict chunks, and mixed-key deduplication.

Billy's compatible consumer changes use existing `flatten` and `take`: message
selection stops at its budget, and search carrier merging avoids a temporary
list and one generator per pair. The taste-balancing loop stays as it was
because proposed compositions were slower. The composition workload here is
synthetic and times selection only; it excludes RPCs, capture, and admission.

Reproduce with a saved baseline `src` tree:

```console
python bench/profiles/iters_backend.py --compare-source /saved/before/src --source src --rounds 9 --output /tmp/iters-backend.json
```

The existing frozen speed ceilings are unchanged. All measured controls and
smaller wins remain in the JSON alongside the faster cases.
