# Process implementation

`adapter.c` registers `_proc_native` and implements admitted POSIX process
creation. It owns encoded arguments and environment data until spawn completes,
sets descriptor isolation and signal defaults, and publishes PID ownership
before returning to Python. Unsupported inputs decline before the subprocess
audit event; accepted failures raise without retrying an audited launch.

`pipes.c` implements bounded captured-output exchange, exported through
`pipes.h`. It owns only temporary output buffers and restores descriptor flags
on all paths. Python keeps descriptor lifetime, text policy and child reaping.

See the [native process guide](../README.md) for capabilities and qualification.
