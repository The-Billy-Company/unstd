# `unstd` package

Stdlib-compatible codecs and focused utility APIs with optional acceleration.
The base install has no third-party runtime dependencies. Each module's README
defines its API, backend requirements, fallbacks, and intentional differences
from stdlib behavior.

- Serialization and formats: [`serde`](serde/README.md), [`pack`](pack/README.md),
  [`toml`](toml/README.md), [`yaml`](yaml/README.md),
  [`compress`](compress/README.md), and [`audio`](audio/README.md).
- Identity, security, and time: [`ids`](ids/README.md),
  [`crypto`](crypto/README.md), [`rand`](rand/README.md), and
  [`time`](time/README.md).
- Data and matching: [`clone`](clone/README.md),
  [`containers`](containers/README.md), [`iters`](iters/README.md),
  [`model`](model/README.md), [`rex`](rex/README.md), and
  [`text`](text/README.md).
- Operating-system helpers: [`fs`](fs/README.md) and [`proc`](proc/README.md).

Import the relevant module directly, for example
`from unstd.serde import jsonx`. [`__init__.py`](__init__.py) resolves
`__version__` lazily from installed metadata; it does not eagerly load the module
groups. [`extras.py`](extras.py) provides deferred `ImportError` placeholders for
capabilities without a fallback. [`py.typed`](py.typed) marks the package's inline
type information for consumers.

Public modules retain their existing import paths. Small domains are cohesive
code leaves with descriptive names such as `audio/pcm.py`, `fs/traversal.py`,
`proc/results.py`, and `serde/encoding.py`. Their guides map owned files and the
current test command; shallow helper packages are removed.

Compression engine integration lives in
[`compress/engines/`](compress/engines/README.md). Its public `gzipx.py` and the
shared `extras.py` module stay on their respective package-root import shelves,
as allowed by the restructure rule for language package roots. Supporting
modules declare an empty `__all__`; naming clarity does not expand the supported
[public API](../../docs/api.md).

All C and Rust source lives under [`native/`](../../native/README.md), outside
the installable Python tree. Private native extension names and `.pyi` stubs
remain beside the wrappers for binary and typing compatibility. Builds without
those extensions retain Python implementations.
See the [project guide](../../README.md) for installation and the
[product suites](../../tests/suites/product/README.md) for module-owned tests.
