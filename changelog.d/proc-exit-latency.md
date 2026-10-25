`proc` uses Linux pidfds and macOS kqueue exit notifications to avoid polling
sleeps after captured pipes close. Bounded waits fall back to stdlib when the
kernel facility is unavailable or restricted. Process creation, captured I/O,
timeouts, cancellation cleanup and the public API retain their existing
contracts; no optional dependency is added. Paired performance evidence lives
in `bench/reports/proc/`.
