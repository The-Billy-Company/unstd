### Added

- `rex.compile_faithful` requires Python regex meaning, including non-multiline
  `$` and nested/POSIX bracket syntax, while retaining native acceleration where
  the parsed pattern and subject support it. Historical `rex.compile` behavior
  remains unchanged.
- `rex.compile_linear` requires Python-faithful native single-match operations and raises
  `rex.LinearRegexError` whenever compilation, a subject, an argument shape or
  a native operation would need backtracking fallback. `requires_ascii` exposes
  subject restrictions. `search`, `match` and `fullmatch` include capture
  projection within a linear subject-length bound for a fixed compiled pattern.
  Iteration, splitting, substitution and scanner operations are always refused
  before matching or callbacks because native suffix rescans can be quadratic.
  Compilation and process limits require a separate application policy.
