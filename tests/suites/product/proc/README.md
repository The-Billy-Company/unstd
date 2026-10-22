# Process runner contracts

Contracts for `unstd.proc`: literal argv, bounded execution, captured output, failure reporting, and direct-child cleanup.

- [unit/](unit/README.md): runner and convenience API guardrails.
- [integration/](integration/README.md): real process boundaries, timeout output, signals, and reaping.
- [regression/](regression/README.md): SIGINT cancellation leaving an unreaped child.

Children run through the selected Python interpreter; no optional product dependencies are required. Signal and process-ownership checks use explicit POSIX guards.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/proc -q
```

See [the module documentation](../../../../src/unstd/proc/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
