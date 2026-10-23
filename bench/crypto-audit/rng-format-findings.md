# Secure-token formatting experiment

Keep `unstd.rand.crypto.token_urlsafe` as the exact `secrets.token_urlsafe`
alias. The isolated candidate preserves OS entropy generation and changes only
the Base64 formatter to installed `pybase64` 1.5.0. It provides no new security
property and fails the no-regression requirement for small calls.

Eleven alternating paired rounds measured complete generation, including the
OS CSPRNG, padding removal, and ASCII decoding. The retained final run measured
default calls at 0.967x wall throughput and 0.961x CPU (descriptive 95% paired
CPU interval 0.935–0.971x), and zero-byte calls at 0.653x CPU. The 32-byte result
was inconclusive. Full-generation CPU ratios were 1.152x at 256 bytes, 1.214x
at 4 KiB, 1.229x at 64 KiB and 1.235x at 1 MiB. Wall measurements were highly
noisy: even explicit `None` measured 0.272x wall with an interval spanning
0.062–1.911x, despite a much smaller CPU change. The unchanged-call CPU control
was 1.002x with an interval spanning 1.0. These wall samples do not establish
portable generation throughput.

Formatting prepared bulk bytes alone measured about 6–8x CPU improvement, which substantially
overstates the benefit to full secure-token generation. Adoption would also
replace an identity-pinned stdlib alias and add an optional codec dependency to
the stdlib-only secure-randomness path. Production code remains unchanged.

Known-byte checks temporarily hooked entropy outside timing, then restored the
real OS provider before all measurements. Negative, overflowing, invalid-type,
boolean, and custom-index arguments retained their behavior; entropy failures
retained exception identity. Exact alias identity remains an explicit reason
the candidate is unsuitable as a transparent replacement.

The complete samples, source/provider/interpreter hashes, contract checks, and
runner source are in [rng-format-results.json](rng-format-results.json). Run
the reusable harness with:

```console
.venv/bin/python -m bench.crypto_rng --output /tmp/rng-format-results.json
```

After timing, host-load metadata collection was made portable to Windows;
that helper runs outside every timer. The report retains the exact executed
source/hash and separately identifies this metadata-only follow-up.
