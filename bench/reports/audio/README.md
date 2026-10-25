# Billy audio evidence

[`billy-audio.md`](billy-audio.md) maps production consumers to the release API,
adoption examples and measured limits. `decode-speed-addition.json` retains the
new public decoder's speed-registry measurement and environment.

`billy-audio.json` records paired complete PCM operations, exact output checks,
wall/current-thread CPU time, throughput and separate traced allocation peaks.
It also retains the source and hashes of Billy's relevant consumer functions.
`wavx-before.py.txt` is the saved wrapping implementation; the driver compiles
these source bytes directly instead of reusing a Python bytecode cache.

Reproduce from the repository root with NumPy installed:

```console
uv run --no-sync python -m bench.profiles.billy_audio \
  --baseline bench/reports/audio/wavx-before.py.txt \
  --billy-root ../billy \
  --output bench/reports/audio/billy-audio.json
```

The PCM16 fixtures include sample rails and interior counts. Decode controls
retain Billy's existing cast-then-divide and cast-then-multiply expressions.
Narration controls include its intermediate `bytes(bytearray)` conversion.
Every operation allocates a complete output; validation and tracing stay outside
timed batches. Seven adjacent pairs alternate their execution order. A changed
source file or mismatched output prevents the report from being written.

These are local shared-host measurements of byte operations, excluding imports,
provider latency, model inference, scheduling and the rest of the voice pipeline.
The report does not establish end-to-end latency gains. Validation and dispatch
can cost more than allocation savings for tiny chunks; assess both time and peak
memory at the caller's actual chunk size.
