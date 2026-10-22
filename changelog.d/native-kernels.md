## Optional C acceleration for clone and text

`clone.deep` now uses a bundled, bounded C walker for eligible exact builtin
trees. Base-install `fuzz.distance` and `fuzz.jaro_winkler` can use bundled
exact-string Levenshtein and Jaro kernels. Public signatures, runtime dependencies,
RapidFuzz selection, Unicode behavior, custom copy protocols and documented
clone alias/cycle behavior are retained. Unsupported inputs and builds without a
compiler use the existing Python implementations.

The paired profile driver in `bench/native_profile.py` checks compatibility and
actual native dispatch before timing. Native-required Python 3.12–3.14 CI,
debug-allocation tests, sanitizer CI, and installed-wheel/fallback tests exercise
the new boundaries.
