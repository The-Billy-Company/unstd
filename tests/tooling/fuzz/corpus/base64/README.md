# Base64 replay inputs

These version-1 inputs exercise `unstd.serde.b64` through the
[Base64 target](../../targets/base64.py). Byte 0 modulo four selects raw decode,
buffer encode, the lenient URL helper, or UTF-8/surrogate-text decode; the rest is
payload. Other selector bits vary buffer type, alphabets, strictness and text
error policy. Empty input selects raw decode of empty bytes.

Reviewed cases cover standard and URL alphabets, missing/excess padding, unused
pad bits, ignored junk, malformed text, MIME line boundaries, alternate alphabet
lengths and longer native-path inputs. The oracle uses independent stdlib
`base64`/`binascii` recipes and checks results and expected input errors.

`seeds.json` authenticates each complete envelope; see the
[corpus rules](../README.md) before editing bytes or promoting a finding. From
the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target base64 --backend stdlib
```

The installed backend requires `pybase64`; setup and campaign options are in the
[fuzz guide](../../README.md).
