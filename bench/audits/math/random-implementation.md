# Random engine implementation qualification

Seven adjacent alternating baseline/candidate pairs retain wall and thread CPU time in [random-implementation-results.json](random-implementation-results.json). The source snapshot is [random-implementation-baseline.txt](random-implementation-baseline.txt); its SHA-256 and the measured driver/current module hashes are recorded in the result. The exact driver is [random-implementation-measured-source.txt](random-implementation-measured-source.txt). No samples were discarded.

| Workload | Paired median CPU speedup | Paired median wall speedup | CPU paired range |
| --- | ---: | ---: | --- |
| `constructor/stdlib/173` | 4.481x | 4.728x | 3.912–4.885x |
| `constructor/stdlib/None` | 1.980x | 1.980x | 1.693–2.243x |
| `constructor/numpy/173` | 2.649x | 2.590x | 1.903–3.153x |
| `constructor/numpy/None` | 1.833x | 1.951x | 1.584–1.915x |
| `reseed/stdlib` | 0.999x | 0.999x | 0.950–1.330x |
| `reseed/numpy` | 0.983x | 1.006x | 0.882–1.070x |
| `explicit_reseed/Philox` | 0.900x | 0.887x | 0.850–1.047x |

Above one favors the candidate. Constructor gains hold in all seven CPU pairs for the default seeded/unseeded constructors. The default NumPy reseed ratio is 0.983x CPU and 1.006x wall with mixed pair signs and 26–28% CPU spread, so this run does not establish a persistent reseed penalty. The added engine-class lookup is required to retain explicit selections without adding an instance slot; no timing-dependent runtime dispatch was added. A repeat is justified if deployment profiles identify reseeding as significant.

Philox reseeding is compared with legacy PCG64, a different algorithm; its 0.900x CPU paired median is a visible tradeoff of the new opt-in, not a default-path change. Constructor options do not promise uniform throughput improvements across algorithms, distributions and platforms.

All nine instance draw instruction sequences, all six bulk helper/wrapper sequences and the three slots are unchanged. Instances remain 56 bytes on this interpreter. Scalar module exports retain the exact stdlib implementation; direct module aliases track standalone stdlib controls. Existing independent-instance method wrappers have measurable overhead (random around 56 ns versus direct stdlib around 25 ns); this pre-existing cost was not hidden.

Fresh subprocess source-module imports were measured in seven alternating pairs and retained separately. They load draw.py directly, not the complete package. Cold import data does not establish a consistent change, and this source-level measurement cannot quantify every deployment startup/cache scenario.

Validation: 254 product random/ID + secure primitive tests passed; scoped Ruff and strict project mypy passed. Every explicit bit generator is compared with its NumPy reference and checked after reseeding. Failed seed atomicity, original selected-item identity, unchanged module/default histories, optional dependency errors, and subclass hooks are covered. Existing crypto aliases and UUID generation/parsing/derivation code remain intact; UUID changes are documentation only.

Reproduce only with an exclusive timing slot:

```console
cp bench/math-audit/random-implementation-measured-source.txt /tmp/unstd-random-qualification.py
cp bench/math-audit/random-implementation-baseline.txt /tmp/unstd-random-baseline.py
.venv/bin/python /tmp/unstd-random-qualification.py --before /tmp/unstd-random-baseline.py --after src/unstd/rand/draw.py --output /tmp/unstd-random-rerun.json
```

Use `--validate-only` to inspect stream/alias/slot evidence and workload registration without timing. This is host-local qualification, not a universal no-overhead proof.
