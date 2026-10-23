Reorganize package internals, native sources and benchmark tooling while
preserving existing public imports, exports and call signatures. Native source
now lives under `native/<package>/src/`; private extension module names remain
unchanged. Benchmark commands retain compatibility entry points, with recorded
evidence and frozen baselines separated from executable tools.

The root README links to a complete public API inventory and architecture guide.
