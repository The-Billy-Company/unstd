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

`raw-unhashable-before-duplicate.bin` minimizes a deterministic mutation finding
(seed 65537, case 1721): a recursive alias used as a mapping key must retain the
safe constructor's earlier unhashable-key error even when the round-trip loader
reaches a later duplicate-key error. Neither acceptance nor an unrelated parser
error satisfies the oracle.

`raw-recursive-merge-finding.bin` retains Atheris case 12353 from GitHub Actions
run 40071352702. The safe parser accepts this self-merge, while ruamel's editable
constructor crashes on its unresolved merge referent. That specific auxiliary
probe limitation leaves both production readers subject to exact safe-parser
parity; it never excuses product exceptions or changed values.

The target is installed-only and requires `ruamel.yaml`. After
[dependency preparation](../../README.md), run from the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target yaml --backend installed
```

`seeds.json` authenticates the complete envelopes; follow the
[corpus rules](../README.md) before changing bytes or promoting findings.
