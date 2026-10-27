# Further complete-decoder investigation

The continued investigation retains the original complete-decoder target and
its full validation requirements. None of the alternatives below establishes
100× on both operating systems. Exploratory mechanisms and proposed ownership
changes are kept separate from the shipped API and installed-wheel evidence in
the [main qualification](../README.md).

## Frozen native scan experiment

The [native mechanism investigation](native-mechanisms.md),
[raw samples](alternatives-results.json) and
[build and source receipt](build-and-source-metadata.json) retain the exploratory
finite-scan comparison. Its [exact driver](measure_alternatives.py.txt) is a
frozen evidence artifact, like the other captured probes in this directory.
The `.py.txt` suffix preserves its measured bytes without treating it as a
maintained benchmark module.

For replay, use a separate checkout matching the recorded source hashes,
Python, dependencies and compiler. Restore the captured driver to its original
path, build `alternatives.c` with the recorded command and its frozen
`reference-adapter.c` source, and supply the private caller capture. From that
checkout root, the driver is restored and invoked as follows:

```sh
mkdir -p .local/pack-ragged-next-native
cp bench/reports/pack/ragged/alternatives/measure_alternatives.py.txt .local/pack-ragged-next-native/measure_alternatives.py
python .local/pack-ragged-next-native/measure_alternatives.py
```

The original `.py` path inside the raw results describes the measured run and
remains unchanged. The receipt maps the retained `.py.txt` artifact to the same
SHA-256 digest.

## Library backend: NumExpr

NumExpr 2.14.2 was evaluated with NumPy 2.5.3 on macOS arm64. Its
[documented](https://numexpr.readthedocs.io/en/latest/user_guide.html) expression
compiler, finite predicate, reductions and thread pool provide two plausible
ways to replace the scan: a fused scalar reduction, or a parallel finite mask
followed by NumPy's `all`. Both expressions were compiled before timing; each
timed call still validates its fresh payload without reusing prior validity.

The [raw complete-decoder samples](numexpr-macos.json) cover 1, 2, 4, 8 and 16
threads. Geometry and boundaries remain with the real native validator;
finiteness uses the selected NumExpr expression. The same captured caller
guards, immutable views and exact application result-class allocation are
inside each call. Every variant passes the existing malformed-input oracle,
including late NaN and infinities.

| Case and NumExpr strategy | Current native complete call | NumExpr complete call |
| --- | --- | --- |
| 64 KiB, one-thread mask + `all` | 1.767 µs | 13.924 µs |
| 4 MiB, eight-thread mask + `all` | 53.922 µs | 199.022 µs |
| 32 MiB, eight-thread mask + `all` | 452.861 µs | 988.598 µs |
| 4 MiB, one-thread fused reduction | 47.683 µs | 1,331.616 µs |

These controls reject NumExpr for this seam. No dependency or runtime backend
was added. Expression compilation is reusable program setup; neither numeric
results nor validation results are cached. The exact
[probe](numexpr_probe.py.txt) is retained. To replay, copy it to
`.local/pack-numexpr-eval.py`, install the recorded versions in an isolated
environment, supply the private capture, and run it from this checkout.

## Array construction

The [frontend experiment](frontends-macos.json) compares current dtype strings
and `frombuffer(...).reshape(...)`, precompiled dtype objects, and direct
two-dimensional `ndarray` views. It measures the complete captured result for
1, 32, 128, 512, 8,192 and 65,536 rows with nine alternating 25 ms rounds.

Neither supported alternative provides a material gain: differences are noise
to roughly 3%, and direct construction regresses the smallest case. Assigning
`array.shape` was also slower and emits NumPy 2.5's deprecation warning, so it
is retained only as a rejected diagnostic. No frontend change was shipped.

Each new variant passed all 91 public decoder tests; 396 independent
dtype/order/exporter cases verify immutable ownership and values. The
[validation receipt](frontends-validation.json) and
[probe](frontends_probe.py.txt) retain those checks and source hashes.

## Native mechanisms

The [native experiment](native-mechanisms.md) evaluates exact NEON unsigned
maximum, 512-bit streaming SVE on the available SME hardware, synchronous
parallel dispatch, and Accelerate reductions. It separates scan-only clocks
from complete caller clocks, retains adverse bit-pattern checks and sources,
and includes a direct same-compiler comparison without a validator proxy.

The unsigned maximum provides a practical medium-frame improvement. Kernel
version 2 adopts it only for NEON float32 chunks of at least 64 KiB, retaining
the existing small-frame path and every other architecture/type path. It adds
no thread pool, dependency, or public API. The
[installed-wheel and current-workflow qualification](native2.md) records this
version on both operating systems, including the maximum recipe capacity.
The exploratory results are not a 100× claim.

## Complete current project CPU pipeline

The [broader workflow replay](project.md) captures the first remote admission,
second caller admission, and final immutable assembly. Reusing the admitted
response and composing its immutable values eliminates duplicate work while
preserving per-response rejection order. The clock includes all three numeric
stages and final result construction. Transport, inference, callbacks and
offload scheduling remain outside the CPU claim.

The proposed ownership seam remains a private prototype. The current tuple
return cannot certify arbitrary external arrays for unchecked composition;
shipping that feature would require a generic admitted-frame contract. Its
measured gain does not establish the requested 100× target.

The [kernel-2 follow-up](native2.md#complete-current-project-cpu-pipeline) runs
the broader replay on both operating systems through their actual installed
wheels. Its source and binary fingerprints are kept with each result.

## Scope of a scheduling comparison

Consumer's current offload policy includes bounded FIFO capacity, cancellation,
copied context, telemetry observations, and ownership until physical worker
completion. Releasing the GIL in a synchronous call does not yield the calling
event-loop thread. Removing those costs from only the candidate would change
the comparison. The CPU measurements here do not count removing that policy
as a decoder acceleration or claim total inference/request speedups.

All private application definitions stay outside these evidence files and
release inputs. Source captures are supplied separately for replay. The
retained observations are measurements, not an impossibility theorem for every
future algorithm or machine.
