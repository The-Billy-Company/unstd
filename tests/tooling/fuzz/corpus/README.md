# Reviewed fuzz corpus

Each target directory contains complete binary inputs for its version-1 fuzz
callback, plus a `seeds.json` manifest. These are synthetic controls and reviewed
regressions, used by fixed replay and as starting inputs for mutation campaigns.
The binary files are target envelopes; a file named for JSON, YAML, WAV or gzip
usually includes selector bytes before its document or stream.

`seeds.json` declares `version`, `target`, `input_version` and `seeds`. Each seed
records its exact `.bin` filename, SHA-256, byte size, human label, `kind`
(`valid`, `invalid` or `boundary`) and reviewed origin. The
[registry](../registry.toml) sets each target's input budget and seed floor.
[Admission](../registry.py) checks every byte and requires all three kinds;
`invalid` seeds pass replay when the target observes the expected rejection.
Only a regular, non-symlinked `README.md` is excluded from corpus discovery.
Other unlisted entries, symlinks, duplicate inputs and altered bytes fail checks.

From the repository root, validate all manifests and replay supported base-mode
inputs with:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target all --backend stdlib
```

Target READMEs describe selectors and retained cases. Use the
[fuzz guide](../README.md) for installed-only targets, dependency preparation,
limits, exact reproduction and reviewed promotion. Keep generated coverage inputs
and campaign reports outside this tree; adding documentation must not add seeds
or change the authenticated corpus digest.
