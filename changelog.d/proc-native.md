`proc` now uses an optional bundled C backend for isolated Linux/macOS process
creation and captured text output without stdin. Current Billy Git, metadata and
log shapes have paired native/Python/stdlib profiles; stdin and unsupported
platform shapes retain stdlib behavior. Captured output, timeout bytes, audit
events, descriptor isolation and kill/reap contracts remain covered by native,
fallback, debug-allocator and sanitizer qualification.
