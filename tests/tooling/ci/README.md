# CI group and report tooling

[`groups.py`](groups.py) composes suite owners into complete, disjoint CI groups
and runs pytest with retained JUnit evidence. [`sharding.py`](sharding.py)
partitions tests deterministically without changing their budgets.
[`campaigns.py`](campaigns.py) reports bounded campaign worker results.

```console
python -m tests.tooling.ci list
python -m tests.tooling.ci check
python -m tests.tooling.ci run contracts -- --require-extras -q
```

[Suite inventory](../suites/README.md) owns paths and evidence kinds; this package
owns CI composition and summaries. See the [test guide](../../README.md) for
required groups, retained reports, and local equivalents.
