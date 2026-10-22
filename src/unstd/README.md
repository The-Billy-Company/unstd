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
groups. [`_extra.py`](_extra.py) provides deferred `ImportError` placeholders for
capabilities without a fallback. [`py.typed`](py.typed) marks the package's inline
type information for consumers.

Optional C sources and stubs live beside their wrappers in `serde/`, `clone/`,
and `text/`. Builds without those extensions retain Python implementations.
See the [project guide](../../README.md) for installation and the
[product suites](../../tests/suites/product/README.md) for module-owned tests.
