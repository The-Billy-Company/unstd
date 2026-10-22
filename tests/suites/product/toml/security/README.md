# TOML malformed-input contracts

`test_malformed_documents.py` rejects a corpus of documents that break rules shared by TOML 1.0.0 and 1.1.0: duplicate keys and tables, empty keys and values, unterminated strings, unknown escapes, raw control characters, malformed numbers, impossible dates, extended inline tables, static arrays reopened as tables, and bare carriage returns. The reader must raise `TOMLDecodeError` naming the hand-identified defect line; the editable parser must also refuse. Each case has a valid twin of the same shape. Deep valid nesting must either decode with the exact structure or raise a catchable parser/recursion error; a subsequent valid read must still succeed.

Reads require no extra. Editable-parser cases require tomlkit from the `toml` extra and follow the shared optional/required backend policy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/security -q
```

See [the module test overview](../README.md) for related evidence.
