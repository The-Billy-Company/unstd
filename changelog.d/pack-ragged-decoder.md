Add `unstd.pack.ragged.RaggedDecoder` for immutable numeric frames containing an
integer boundary table and contiguous rows. It validates geometry, monotonic
boundaries, and optional float finiteness, sharing exact immutable bytes or
snapshotting other exporters before validation. The optional CPython validator
uses NEON/SSE2 or portable integer-bit scans without a NumPy C ABI; base installs
retain a stdlib implementation and immutable tuple results. No input validity
or decoded-result cache is used.

Large NEON float32 scans use an exact unsigned maximum of magnitude bits,
reducing inner-loop work while retaining strict NaN/infinity rejection. Smaller
frames retain the existing classifier. Complete installed-wheel and current
consumer-pipeline qualification on Linux and macOS is documented separately;
the requested 100× complete-decoder target remains unmet.
