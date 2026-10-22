# Safe YAML replay inputs

These version-1 envelopes drive the [YAML target](../../targets/yaml.py) and
`unstd.yaml`. Byte 0 modulo four selects raw bytes, an independently known
JSON-subset model, an unknown/unsafe explicit tag, or an acyclic alias graph.
Remaining bytes supply source or model entropy. Empty input selects an empty
safe stream. Generated aliases have depth 1..24 and fanout 2..8; graph checks
track identity instead of expanding shared nodes.

Cases cover safe scalars, binary/timestamps, legal merges, duplicate/semantic key
collisions, unknown/Python tags, malformed documents, recursive/shared aliases
and the retained invalid-date finding. Raw results/errors compare with a
separately configured pure safe ruamel parser; an independent round-trip check
identifies merged duplicates when that reference misses them. Generated models
and tag refusal also check the wrapper's own safe-construction policy.

The target is installed-only and requires `ruamel.yaml`. After
[dependency preparation](../../README.md), run from the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target yaml --backend installed
```

`seeds.json` authenticates the complete envelopes; follow the
[corpus rules](../README.md) before changing bytes or promoting findings.
