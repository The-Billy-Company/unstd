"""Safe YAML 1.2 codecs and faithful backend contracts, loaded on demand.

``pyyaml`` and ``ruamel`` are the original backend modules, cached unchanged.
Native value and editable-document helpers retain the YAML 1.2 policy.
"""

__all__ = [
    "YAMLError",
    "YamlScalar",
    "YamlValue",
    "dump",
    "dump_all",
    "dumps",
    "load",
    "load_all",
    "loads",
    "parse",
]


def __getattr__(name: str) -> object:
    """Resolve one backend noun or bind the actual native codec exports once."""
    if name in ("pyyaml", "ruamel"):
        try:
            if name == "pyyaml":
                import yaml as backend
            else:
                from ruamel import yaml as backend
        except ImportError as error:
            msg = f"unstd.yaml.{name} requires the yaml extra - install 'unstd[yaml]'"
            raise ImportError(msg) from error
        globals()[name] = backend
        return backend
    if name not in __all__:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    from unstd.yaml import codec

    globals().update((key, getattr(codec, key)) for key in __all__)
    return globals()[name]


def __dir__() -> list[str]:
    """Expose the complete API without importing either backend."""
    return sorted({*globals(), *__all__, "pyyaml", "ruamel"})
