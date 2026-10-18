# Process cancellation regressions

Status: **active**. A real SIGINT interrupts `run`, `capture` and `run_ok` while
a child owns captured pipes. Independent `waitpid`/liveness checks prove that
cancellation propagates only after the direct child is killed and reaped; a
subsequent command proves recovery. The experiment uses a readiness handshake
and an outer process deadline, not a timed guess.

Run `uv run --no-sync python -m tests.tooling run regression -- -q`.
