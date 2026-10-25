# Private process implementation

`results.py` owns captured outcomes and process errors. Result classes keep
their `unstd.proc` identity for existing imports and pickles.

`backend.py` subclasses stdlib `Popen` at its private bounded `_wait` seam.
Linux pidfds and macOS kqueue notifications avoid the polling sleeps between
pipe EOF and child exit. Native handles close on completion, timeout, and
cancellation. Unsupported or failed native setup uses the remaining deadline
with the stdlib wait. Stdlib retains process creation, pipe multiplexing, text
decoding, signal handling, waitpid locking and final reaping. No subprocess
globals, signal handlers, or shared worker threads are changed.

The public `unstd.proc` module owns execution orchestration, validation, timeout
cleanup, and logging. The private stdlib seam is covered by lifecycle and
cancellation tests across the supported Python versions.
