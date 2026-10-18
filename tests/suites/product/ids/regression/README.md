# UUID fork regression

Status: **active**. Fork while another parent thread owns the Python UUIDv7
counter lock, then mint through both public string formats in the child. The
child must own a fresh unlocked counter and freshly seeded state; the parent's
lock, state and selected backend remain intact. This reproduces the deadlock
found by the controlled concurrency lane and runs in ordinary pytest runs.

A child alarm, fixed worker deadline and unconditional process ownership bound
the historical failure. The concurrency lane also deliberately restores the
poisoned lock to prove that its deadlock watchdog and reaping controls work.

Run `uv run --no-sync python -m tests.tooling run regression -- -q`.
