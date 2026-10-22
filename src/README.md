# Python source tree

[`unstd/`](unstd/README.md) is the installable package. This repository uses a
`src` layout: development tools and tests stay at the repository root, outside
the package. Python 3.12 or newer is required.

Install the checkout from the repository root with `uv sync --locked --group dev`;
add `--all-extras` to install every optional backend. Then use
`uv run --no-sync python` to exercise that environment. Build configuration and
dependency extras are defined in [`pyproject.toml`](../pyproject.toml);
[`hatch_build.py`](../hatch_build.py) adds optional CPython extensions.

See the [project guide](../README.md) for public module groups and the
[test guide](../tests/README.md) for validation commands.
