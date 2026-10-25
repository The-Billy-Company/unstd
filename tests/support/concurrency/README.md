# Concurrency-history support

[`filesystem.py`](filesystem.py) coordinates competing writers and independent
readers. [`ids.py`](ids.py) records UUID contention and checks interval histories.
[`shared_state.py`](shared_state.py) exercises reviewed shared-object schedules
inside deadline-owned interpreters.

Independent models and controlled boundaries determine expected results;
timeouts bound worker lifetime. Owning assertions live in the
[concurrency suite](../../suites/assurance/adversarial/concurrency/README.md),
and [campaign tooling](../../tooling/concurrency/README.md) owns recorded runs.
