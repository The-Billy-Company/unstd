# Process integration evidence

Run `uv run pytest tests/suites/product/proc/integration -q` from the repository
root. This leaf belongs to the `integration` inventory lane and runs in the
normal suite without optional product dependencies.

`test_process_boundaries.py` runs real Python children that report exact argv,
Unicode cwd, isolated environment, and stdin facts. Hostile argument values cover
empty tokens, quoting, whitespace, Unicode, shell substitutions, redirections,
wildcards, option-like strings, and trailing backslashes; fixture directories
prove no shell-created artifact appeared. Other cases cover missing executables,
positive finite timeout validation before spawn, simultaneous multi-megabyte
stdout/stderr with megabyte stdin, nonzero checked/unchecked outcomes, both-stream
codec policies, exact completion records, and logging failures after completion.

`test_process_lifecycle.py` observes partial stdout/stderr bytes in the real
`TimeoutExpired`, checks the configured timeout and command, proves the direct
child was killed and reaped on POSIX, checks all three convenience entrypoints,
and confirms a later successful call. POSIX signal termination preserves negative
return codes and captured output. Unencodable strict stdin also leaves no child.
Timeouts do not emit a misleading completion record; user logging exceptions
propagate after the completed child has been reaped. A POSIX regression retains
the captured output pipes in a live descendant and still requires the configured
timeout to return; fixture process-group cleanup then kills that descendant.

Potentially blocking scenarios run in an outer Python worker with a hard parent
deadline. Cleanup reaps the worker and kills its fixture-owned process group on
POSIX, including children if the scenario itself fails. Tests use the selected
Python interpreter rather than assuming shell tools are installed. Signal/PID
inspection is guarded precisely on POSIX; portable outcome assertions run on all
platforms. The product promises to terminate its direct child on timeout;
descendant-tree termination is not a product guarantee.
