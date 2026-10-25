`proc` now uses an optional bundled C backend for isolated macOS process creation
and Linux/macOS captured text output without stdin. Linux retains its optimized
stdlib launch path. Current Git, metadata and
log shapes have paired native/Python/stdlib profiles; stdin and unsupported
platform shapes retain stdlib behavior. Captured output, timeout bytes, audit
events, descriptor isolation and kill/reap contracts remain covered by native,
fallback, debug-allocator and sanitizer qualification.
