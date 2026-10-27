Add `unstd.dataset` for immutable indexed JSON corpora. Build a portable
snapshot in one streaming pass, then count, index, slice, select rows in request
order, or process bounded batches without retaining the corpus. Files publish
atomically and support explicit SHA-256 integrity checks. Optional bundled C
kernels accelerate checked offset lookup and copying; portable installs retain
the same API. Include independent format/native parity tests and generic corpus
benchmarks reporting construction cost, gains, and losses.
