# Clone measurements

- [clone-ids-results](clone-ids-results.md)
- [clone-cpu-results](clone-cpu-results.md) — paired CPU/wall evidence against the previous native implementation
- [clone-common-cpu-results](clone-common-cpu-results.md) — further common dict/list and dataclass CPU work, measured through public Python entrypoints
- [clone-foundry-results](clone-foundry-results.md) — Linux installed-wheel native qualification, common-shape performance on Python 3.13/3.14, and the ongoing latency guard
- [clone-sequence-results](clone-sequence-results.md) — scalar prefixes and adjacent aliases, stricter paired latency guards, server scaling and sanitizer evidence

See the [evidence index](../README.md) and [benchmark guide](../../README.md).

- [clone-shared-identity-results](clone-shared-identity-results.md) — shared-graph
  savings and distinct-tree memo overhead for identity-preserving `deep`

- [clone-shared-identity-optimized-results](clone-shared-identity-optimized-results.md) — pointer-keyed memo savings against the exact first identity-preserving implementation, with GC on and off

- [clone-dataclass-identity-results](clone-dataclass-identity-results.md) — shared-memo dataclass allocation/state copying and weak admission cache, compared with the exact pre-change implementation

- [clone-consumer-model-v2-results](clone-consumer-model-v2-results.md) — local model, slotted-record and graph consumers, with matched stdlib controls, allocation diagnostics and explicit 100× target gaps

- [clone-consumer-temporal-efb2ae10-results](clone-consumer-temporal-efb2ae10-results.md) — first frozen 57-case candidate; eight 100× results, all target misses, a failed regression gate and retained qualification limits
