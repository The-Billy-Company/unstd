# Cache integration contracts

Fixture-owned interpreter workers prove reuse across process lifetimes,
same-key fill exclusion, simultaneous independent producers, unrelated writes
during production, ordered mutation and clearing, alias and fork ownership,
lock timeout recovery, and recovery after a killed producer.
Every worker has a parent deadline and unconditional cleanup.
