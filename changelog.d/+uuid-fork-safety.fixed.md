Reset the Python UUIDv7 fallback's lock and counter state after POSIX fork so a
child can generate IDs even when a vanished parent thread held the counter lock.
Preserve the parent's state and selected UUID backend; pin the deadlock with a
bounded ordinary regression and controlled concurrency histories.
