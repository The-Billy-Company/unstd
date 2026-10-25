"""Optional CPython kernels for compatible sorted collection subclasses."""

KERNEL_VERSION: int

def install(cls: type, base: type, keyed: bool = False) -> None: ...
