Accelerate existing SortedList prefix/suffix slice deletion and increasing
integer list/tuple updates on Linux and macOS. Stage replacement blocks before
commit, reject storage aliases and callback-bearing layouts, revalidate after
allocation, and retain upstream behavior for unsupported cases. Add paired
mutation benchmarks, deterministic allocation-failure and reentry tests, and
test-only sanitizer hooks. Keep scalar mutation algorithms upstream. Tighten
read-query fallback ownership so comparator, metadata and keyword callbacks
retain upstream finalizer timing.
