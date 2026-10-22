# Regex API contracts

`test_rex.py` compares public matching, substitutions, splitting, flags, bytes/Unicode behavior, errors, and cache operations with stdlib `re`. It checks when native compilation is accepted and when unsupported features use the stdlib fallback.

Native-only cases require the `rex` extra. The adversarial matching comparison runs in a bounded subprocess; fallback matching retains stdlib backtracking behavior.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rex/unit -q
```

See [the module test overview](../README.md) for related evidence.
