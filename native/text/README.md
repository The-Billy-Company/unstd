# Native text metrics

| Source | Role |
| --- | --- |
| [`src/adapter.c`](src/adapter.c) | Default C implementation and CPython binding for bounded Levenshtein distance and Jaro similarity. |
| [`rust/adapter.c`](rust/adapter.c) | Alternative CPython binding that calls the Rust kernels; selected at build time by `UNSTD_TEXT_RUST=1`. |

Both build as `unstd.text._text_native`. [`fuzz.py`](../../src/unstd/text/fuzz.py)
owns scorer dispatch and Python fallback; installing RapidFuzz selects its
public distance and Jaro–Winkler scorers. The bundled kernels require exact
strings and return `NotImplemented` outside their work budgets.

See [`src/README.md`](src/README.md) for the C limits and
[`rust/README.md`](rust/README.md) for `Cargo.toml`, `Cargo.lock`, `src/lib.rs`,
`src/core.rs`, ABI ownership and build/test commands. Keep the two implementations'
Unicode semantics, refusal paths and Jaro arithmetic aligned when changing either.
