# Consumer use case: reuse completed evaluation summaries

The Consumer checkout contains two prospective consumers of the new cache. These
are source-verified opportunities; this change adds the reusable unstd package
and does not migrate Consumer's application code.

`services/evaluation/evaluation/trainer/evaluate.py:283`, `summarize`, materializes
per-example, chain-step, abstention, and pass-k rows. It uses Polars aggregation
to produce `EvalSummary`, including per-capability and per-track metrics.
Repeated postprocessing of the same completed evaluation could persist
`summarize(...).model_dump(mode="json")`, then reconstruct the contract with
`EvalSummary.model_validate` on subsequent runs.

Use a key containing the digests of **all completed input streams**, the
scoring/contract version, `eval_loss`, and pass-k options. Computing an input
digest still reads the input unless an immutable artifact manifest already
supplies it. Version the namespace when aggregation or output semantics change.
Cached values must satisfy the package's finite JSON value contract. This
boundary concerns derived summaries; live model generations need their own
fresh evaluation behavior.

`benchmarks/harness/loaders/fixtures.py:155`, `load_tool_use_corpus`, parses
JSONL and validates tools and expected calls. Both the agent-orchestration
`_bench_tool_use` and long-horizon `_bench_tool_drift` adapters call it.
Large immutable corpora could reuse validated JSON under a key containing
the exact source-content digest and validator version. Small fixtures may cost
less to parse than a disk-cache lookup; profile the actual corpus first.

[`consumer-consumers.json`](consumer-consumers.json) retains the inspected source-file
hashes and symbol locations. No runtime data, model responses, credentials, or
external source bodies are copied into this repository.

The separate [`reuse.json`](reuse.json) measures a **synthetic** compressed-eval
summary workload using existing `gzipx` and `ndjson` APIs. Each process checks
its output against values independently accumulated during fixture creation.
It proves persisted reuse and avoided producer work, and measures the elapsed
time on this worker. It does not measure Consumer's Polars summarizer or establish
a speedup for a deployed Consumer service.
