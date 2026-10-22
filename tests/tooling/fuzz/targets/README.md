# Executable fuzz targets

Each module exposes deterministic `fuzz(data: bytes) -> None` assertions for one
registered parser, codec or token surface. The
[registry](../registry.toml) owns identities, supported backends, required imports,
input version and byte budget. Module docstrings define the version-1 envelope;
selector bytes are part of the input, and empty input has a defined case.

| Module | Inputs and oracle |
| --- | --- |
| [`json.py`](json.py) | Raw byte/text JSON or bounded generated values; stdlib values, order and diagnostics. |
| [`base64.py`](base64.py) | Raw/text decode, encode buffers and URL helper; stdlib Base64 recipes. |
| [`ndjson.py`](ndjson.py) | Physical rows or generated records; independent per-line JSON and callback histories. |
| [`structs.py`](structs.py) | Fixed bounded schemas and ordering options; raw `msgspec` wrapper parity. |
| [`tokens.py`](tokens.py) | Arbitrary text or generated authentic/malformed envelopes; raw keyed BLAKE3 and stdlib Base64. |
| [`pack.py`](pack.py) | Numeric kind/order selectors and samples; independent `struct` bytes/values. |
| [`wav.py`](wav.py) | Generated PCM16, raw RIFF or corrupt headers; `wave`, exact PCM and arithmetic scaling. |
| [`toml.py`](toml.py) | Raw text or generated TOML-1.0 model; `tomllib` and known writer models. |
| [`yaml.py`](yaml.py) | Raw safe YAML, known values, refused tags or aliases; ruamel reference and security-policy checks. |
| [`rex.py`](rex.py) | Bounded shared-grammar execution or arbitrary compile-only patterns; `re` results/errors. |
| [`gzip.py`](gzip.py) | Raw/generated/corrupt members; stdlib gzip and bounded expansion preflight. |

Run from the repository root after the [fuzz guide's setup](../README.md):

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target all --backend stdlib
uv run --no-sync python -m tests.tooling.fuzz replay --target all --backend installed
```

`structs`, `tokens` and `yaml` support installed mode only. The worker handles
backend admission and execution bounds; calling a callback directly omits that
supervision. Raw malformed inputs are expected controls, and target admission
limits bound campaign resources rather than redefining public API behavior.
Inputs never select executable code, arbitrary schemas or caller-supplied hooks.
[Reviewed corpus manifests](../corpus/README.md) authenticate replay data; tests
of the oracles and runner live in [fuzz gates](../../../suites/assurance/gates/fuzz/).
Atheris instruments Python coverage; these callbacks do not establish native
coverage or exhaustive parser correctness.
