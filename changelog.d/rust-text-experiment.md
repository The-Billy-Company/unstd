Added an opt-in Rust prototype for the bounded text kernels and a paired,
output-checked C-versus-Rust comparison harness. Default and release builds keep
the existing C implementation: the measured Rust prototype has short-Jaro
latency and extension-size overhead and does not meet the no-overhead gate.
Rust builds are selected explicitly with `UNSTD_TEXT_RUST=1` and retain the
public API, Unicode behavior, resource bounds and portable fallbacks.
