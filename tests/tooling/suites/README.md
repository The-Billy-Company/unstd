# Suite inventory and runner

[`inventory.toml`](inventory.toml) assigns each test file exactly one active
owner and records planned lanes separately. [`runner.py`](runner.py) validates
ownership, lists lanes, and applies each selected lane's paths and marker to
pytest. Planned lanes cannot run.

```console
python -m tests.tooling list
python -m tests.tooling check
python -m tests.tooling run parity -- -q
```

The root tooling entry point and `python -m tests.tooling.suites` share this
runner. [CI composition](../ci/README.md) groups owned lanes; the
[test guide](../../README.md) explains evidence policy and opt-in execution.
