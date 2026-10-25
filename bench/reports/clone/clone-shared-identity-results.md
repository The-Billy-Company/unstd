# Shared-reference preservation measurements

`clone.deep` now shares one identity memo across structural reconstruction and
foreign copy protocols. Repeated references reach one cloned object, independent
of the source. The native graph path copies each shared builtin container once;
it checks live values during copying and commits staged memo entries on success.

These local CPython 3.13 measurements compare the public `deep` call with the
previous builtin fast path, recreated from `native.clone_checked` plus its prior
atom, type and result checks. The baseline is limited to eligible builtin data;
it is not a saved complete pre-change implementation. The tree entrypoint's
algorithm remains available for `asdict`. Stdlib `copy.deepcopy` is the control.

| Input | Current `deep` | Prior builtin path | Stdlib | Current / prior CPU |
| --- | ---: | ---: | ---: | ---: |
| Three-field scalar dict | 0.27 µs | 0.11 µs | 1.23 µs | 2.4× slower |
| 10,000 scalar list items | 30.30 µs | 38.04 µs | 1.03 ms | 1.26× faster |
| 200 distinct nested records | 77.29 µs | 40.58 µs | 421.27 µs | 1.90× slower |
| One 1,000-item list referenced 500 times | 16.40 µs | 2.52 ms | 147.62 µs | 153× faster |

The shared graph gains from copying its child list once. The earlier path
allocated 500 child lists and copied their 500,000 slots. Distinct trees pay
for the identity memo, so preserving references has a measurable cost on those
shapes. All four current cases remain faster than stdlib in this local sample;
these timings do not establish a universal performance guarantee.

Measurements use nine alternating-order rounds with prepared inputs, process
CPU timers and output destruction included. Cyclic GC is disabled only during
timed batches. Every output is checked for exact types, values, source isolation
and the implementation's expected alias behavior outside timing.

[Raw results](clone-shared-identity-py313.json) include per-round CPU/wall samples,
source and extension fingerprints, and the complete measurement harness under
`harness_source`. These samples were taken on the local development machine
while other development work was possible, so small timing differences should
be checked again on a quiet performance worker.
