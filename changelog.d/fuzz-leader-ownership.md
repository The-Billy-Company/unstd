Fuzz replay and Atheris supervisors now observe POSIX worker exit without reaping
the leader until its owned process group has been cleaned up. This closes the
released-PID reuse window before group signaling; cleanup errors still fail.
macOS Python 3.12 uses a contained stdlib `ctypes` adapter for the OS `waitid`
operation that its Python wrapper does not expose.
