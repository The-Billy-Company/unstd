"""Public split modules exercised by the required installed-extra consumers."""

from __future__ import annotations


EXTRA_MODULES: dict[str, tuple[str, ...]] = {
    "compress": (
        "unstd.compress.engines.native",
        "unstd.compress.engines.reader",
    ),
    "containers": ("unstd.containers.ordering",),
    "crypto": ("unstd.crypto.hashing",),
    "toml": ("unstd.toml.dialect",),
    "yaml": ("unstd.yaml.codec", "unstd.yaml.parser"),
}
