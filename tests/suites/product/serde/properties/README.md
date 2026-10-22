# Generated JSON and Base64 parity

`test_serde_parity.py` generates JSON values/options and compares exact encodings, decoded types/order, floating-point signs, and diagnostics with stdlib `json`. `test_b64_parity.py` checks codec results and failures against stdlib `base64`, including alternate alphabets, padding, and the convenience URL decoder.

Base64 explicitly selects stdlib and available pybase64 paths; JSON exercises the active public codec. Shared Hypothesis profiles bound generation cost; those bounds do not narrow the public API contract.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/properties -q
```

See [the module test overview](../README.md) for related evidence.
