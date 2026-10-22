# Regex execution boundaries

`test_regex_boundary.py` runs hostile regular patterns in killable workers and records actual native search calls, with successful controls using the same compiled shapes. Unsupported native features are separately checked against stdlib behavior.

The suite also checks Python syntax rejection and callback exception identity without replaying effects. Native safety probes require the `rex` capability; stdlib fallback is not claimed as linear-time matching.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rex/security -q
```

See [the module test overview](../README.md) for related evidence.
