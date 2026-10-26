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

Public modules retain their existing import paths. Code lives in cohesive domain
leaves, with descriptive names such as `audio/pcm.py`, `fs/traversal.py`,
`proc/backend.py`, and `serde/encoding.py`. Small modules stay together instead
of growing a helper package. Each domain README maps its owned files, describes
relationships, and gives its test command.

Compression spans distinct engine and archive concerns, so its guide indexes
[`engines/`](compress/engines/README.md) and
[`libarchive/`](compress/libarchive/README.md). Its public format modules and
typing stubs stay on the package's import shelf. The shared `extras.py` module
similarly lives beside the domain packages: these are the restructure rule's
language-package-root cases, preserving meaningful imports without one-file
wrapper packages. The [public API inventory](../../docs/api.md) lists supported
exports; support modules do not become public APIs merely through renaming.
Supporting modules declare an empty `__all__`; their named internal imports
remain available while public API discovery follows the existing facades.

All C and Rust source lives under [`native/`](../../native/README.md), outside
the installable Python tree. Private native extension names and `.pyi` stubs
remain beside the wrappers for binary and typing compatibility. Builds without
those extensions retain Python implementations.
See the [project guide](../../README.md) for installation and the
[product suites](../../tests/suites/product/README.md) for module-owned tests.
