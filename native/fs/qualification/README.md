# Linux native filesystem qualification

`observe.c` is a test-only ELF interposition seam used by the public fs
integration contracts. It performs real successful I/O, records scratch-file
mode and sync/rename ordering, injects bounded failures and short writes, and
delivers a signal synchronously after a selected syscall completes. This gives
deterministic cancellation boundaries without sleeps or production fault hooks.

The pytest fixture builds it in a private temporary directory when a Linux
compiler is available. Existing sanitizer preloads retain their original
position before this observer. It must never be loaded during benchmarks.
