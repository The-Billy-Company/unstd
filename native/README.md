# Native experiments

[`text/`](text/README.md) contains an opt-in Rust implementation of the bundled
text kernels, with a C adapter for CPython. It is selected at build time with
`UNSTD_TEXT_RUST=1`; ordinary builds use the C implementation under
[`src/unstd/text/`](../src/unstd/text/README.md).

The default JSON, clone, and text C extensions live beside their Python wrappers
under [`src/unstd/`](../src/unstd/README.md). [`hatch_build.py`](../hatch_build.py)
builds the extensions, and the [comparison report](../bench/rust-text-results.md)
records the Rust experiment's measured tradeoffs. Cargo's generated `target/`
directories are ignored build output.
