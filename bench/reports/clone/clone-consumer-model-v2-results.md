# Consumer clone iteration: fused models, second candidate

We measured thirteen synthetic consumer shapes through public `clone.deep` and `clone.asdict`. All outputs pass the same exact-type, ordered-state, alias, cycle, hook and source-isolation checks as their stdlib controls. The original thirty common cases remain the default suite; this optional set adds small and large models, frozen slotted records and shared graph controls.

The fused model path improves ten-image attachments and forty scalar materials by about 1.9× over the pre-task source baseline. The tiny models gain less. The dataclass alias and foreign copy-hook cases take about 1.33× as much CPU time; these remain open findings. Every case misses the requested 100× stdlib target.

## Matched results

This is one local arm64 CPython 3.14.9/Pydantic 2.13.5 run, with GC enabled and seven alternating rounds. Speedups use the median of matched round ratios; current CPU is the median time in microseconds. The 100× target CPU time is stdlib's median divided by 100, in nanoseconds. Further CPU improvement is the additional factor still needed to reach the target.

| Case | Current CPU µs | vs before CPU | vs before wall | vs stdlib CPU | vs stdlib wall | 100× target ns | Further CPU needed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `question-tiny` | 3.101 | 1.11× | 1.15× | 1.33× | 1.30× | 41.3 | 75.0× |
| `question-nested-100` | 32.895 | 1.04× | 0.96× | 3.47× | 2.92× | 1136.0 | 28.9× |
| `question-private-alias` | 4.091 | 1.10× | 1.04× | 1.53× | 1.60× | 62.7 | 65.4× |
| `attachment-empty` | 1.748 | 1.28× | 1.27× | 1.80× | 1.87× | 31.3 | 55.7× |
| `attachment-images-10` | 10.771 | 1.91× | 2.00× | 2.14× | 2.23× | 229.0 | 46.8× |
| `materials-scalar-40` | 42.790 | 1.88× | 1.97× | 2.24× | 2.01× | 948.3 | 44.6× |
| `materials-data-40` | 191.785 | 1.22× | 1.32× | 3.06× | 3.13× | 5821.2 | 32.7× |
| `change-slots-tiny` | 3.743 | 1.22× | 1.07× | 1.13× | 1.28× | 42.4 | 88.3× |
| `change-slots-1000` | 4975.545 | 1.47× | 1.72× | 1.40× | 0.78× | 69284.0 | 71.5× |
| `dataclass-field-alias` | 1.381 | 0.75× | 0.71× | 2.79× | 2.67× | 38.6 | 35.8× |
| `asdict-field-alias` | 1.054 | 0.96× | 0.95× | 2.01× | 3.29× | 21.0 | 49.9× |
| `builtin-cycle` | 2.068 | 0.98× | 0.96× | 0.80× | 0.77× | 15.7 | 125.2× |
| `copy-hook-alias` | 1.659 | 0.76× | 0.68× | 0.94× | 0.82× | 15.8 | 106.0× |

The cycle control is slower than stdlib on both clocks. The copy-hook case is also slower than stdlib. Slots 1000 improves over the source baseline, but its wall result loses to stdlib while its CPU result wins; background scheduling and allocator noise limit the wall inference. The data-bearing question's CPU improvement over before is small and its matched wall result loses by 4%. We make no universal improvement claim or 100× claim from this rung.

The raw file also retains the actual installed 4.0.2 build as `original`. It runs under the same interpreter and dependency set as both source snapshots. Its Python wrapper matches `before`; its native binary differs. We keep that distinction in the hashes and do not compare timings from separate environments.

## Wiring and semantics

The public native-route receipt observes the fused model path only for the candidate's own backend capability. Before and installed 4.0.2 report that capability false. After warmup, the model proof observes a successful native walk, requires the canonical admitted model handler, and rejects any Python `_model` reconstruction frame. It also checks a private/public alias against stdlib; a callback that silently falls back cannot satisfy the proof.

The checks preserve shared references across fields and private state, foreign hooks using the same memo, builtin cycles, and equal-but-distinct source nodes. `asdict` keeps stdlib's independent field-copy behavior. The model controls use synthetic shapes and contain no application code or saved user data.

## Allocation diagnostics

A separate earlier run measured the set candidate against deliberately incomplete allocation controls. `object.__new__` allocates exact record instances; a second control adds shallow state containers; the C control allocates counted empty structural nodes. They omit traversal, full content copying and other required clone work. Python call and result-list overhead also contribute, so these numbers are not formal lower bounds or proof that 100× is impossible.

In that run, empty-node C control CPU was 2.5–7.2× the stdlib/100 target on the five selected cases. The tiny question's exact-object-only control was 121ns against a 41ns target. These diagnostics show that allocation costs deserve attention; algorithmic wins and the actual complete operation still need direct measurements. The full controls and their omissions remain in the raw file. Wall timing was heavily interrupted, so this section uses CPU only.

## Reproduce

Use the same Python and dependencies for every source variant. Extract the saved sources; rebuild `current/adapter.c` and `before/adapter.c` with the same interpreter, local native build flags and extension suffix. The archive intentionally omits native binaries; their measured hashes are retained. Installed 4.0.2's matching C source was unavailable, so exact binary reproduction of that optional historical control requires the retained installed artifact.

```sh
python -m bench.profiles.clone_common_cpu \
  --source-dir CURRENT_SNAPSHOT --compare-dir BEFORE_SNAPSHOT \
  --original-dir PUBLISHED_4_0_2_SNAPSHOT \
  --infer-native-bindings --require-native --gc-enabled \
  --consumer-cases --include 'consumer-*' \
  --target-speedup 100 --rounds 7 --output result.json
```

The target is reported separately from the before/after regression guard. Add `--enforce-target` to make a missed 100× target fail after saving all results. This rung did not run the thirty-case final regression guard; qualification follows the final runtime candidate.

[Raw model results](clone-consumer-model-v2-py314-gc.json), [allocation diagnostics](clone-consumer-allocation-controls-py314-gc.json), [source snapshot](clone-consumer-model-v2-sources.tar.gz), and [receipt](clone-consumer-model-v2-receipt.json) preserve the measurements and identities. The benchmark gate suite passed 96 tests; strict Mypy and Ruff passed on the five changed Python files. The receipt records the hashes and the limits of this local iteration.
