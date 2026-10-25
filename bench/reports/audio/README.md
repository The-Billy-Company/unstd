# Audio evidence

The consumer audit retained in this directory maps production consumers to the release API,
adoption examples and measured limits. `decode-speed-addition.json` retains the
new public decoder's speed-registry measurement and environment.

The paired audio report in this directory records paired complete PCM operations, exact output checks,
wall/current-thread CPU time, throughput and separate traced allocation peaks.
It also retains the source and hashes of the relevant consumer functions.
`wavx-before.py.txt` is the saved wrapping implementation; the driver compiles
these source bytes directly instead of reusing a Python bytecode cache.

Reproduction requires NumPy and the saved baseline wrapping implementation.
The audio consumer profiler is in the [profile directory](../../profiles/).

The PCM16 fixtures include sample rails and interior counts. Decode controls
retain the existing cast-then-divide and cast-then-multiply expressions.
Narration controls include the intermediate `bytes(bytearray)` conversion.
Every operation allocates a complete output; validation and tracing stay outside
timed batches. Seven adjacent pairs alternate their execution order. A changed
source file or mismatched output prevents the report from being written.

These are local shared-host measurements of byte operations, excluding imports,
provider latency, model inference, scheduling and the rest of the voice pipeline.
The report does not establish end-to-end latency gains. Validation and dispatch
can cost more than allocation savings for tiny chunks; assess both time and peak
memory at the caller's actual chunk size.
