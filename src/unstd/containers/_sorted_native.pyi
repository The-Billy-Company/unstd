"""Optional CPython kernels for compatible sorted collection subclasses."""

KERNEL_VERSION: int

def install(cls: type, base: type, keyed: bool = False) -> None: ...

def mutation_stats(cls: type, *, reset: bool = False) -> dict[str, int]: ...
