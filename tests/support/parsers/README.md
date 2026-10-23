# Bounded hostile-input workers

[`cases.py`](cases.py) controls parser subprocesses, input/output budgets, and
parent assertions. [`worker.py`](worker.py) executes one parser or regex request
in an isolated interpreter with platform resource limits and backend observations.

Hostile inputs and native calls stay in deadline-owned workers. Owning product
suites define expected behavior; [worker gates](../../suites/assurance/gates/packaging/README.md)
verify import selection and containment.
