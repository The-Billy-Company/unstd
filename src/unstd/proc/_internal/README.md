# Private process implementation

`results.py` owns captured outcomes and process errors. Result classes keep
their `unstd.proc` identity for existing imports and pickles.

`backend.py` subclasses stdlib `Popen` at its private spawning, communication and
bounded-wait seams. The optional `_proc_native` extension admits ordinary POSIX
spawning with descriptor isolation and captures stdout/stderr without stdin.
Unsupported shapes retain stdlib execution. Stdlib keeps text decoding, SIGINT
grace, waitpid synchronization and final reaping.

Linux pidfds and macOS kqueue notifications avoid polling sleeps between pipe
EOF and child exit. Notification handles close on completion, timeout and
cancellation; unavailable facilities fall back with the remaining deadline.
Production code changes no subprocess globals, signal handlers or worker threads.

The public `unstd.proc` module owns execution orchestration, validation, timeout
cleanup, and logging. The private stdlib seam is covered by lifecycle and
cancellation tests across the supported Python versions.

The [native source guide](../../../../native/proc/README.md) explains owned
argument/environment references, private output buffers, audit handling and
sanitizer qualification.
