# Focused process build

`build.py` builds the proc module independently of other native domains, using
the invoking Python interpreter's SDK. It compiles `adapter.c` and `pipes.c`,
links libc's dynamic symbol lookup support on Linux, and atomically installs a
fresh extension inode under the selected source root.

```console
uv run --no-sync python native/proc/experiments/build.py --output src
```

Use a temporary `--output` directory for isolated experiments. The helper does
not change the public API or select a different interpreter. Benchmark workers
must start after rebuilding to use the new extension.


Use `--sanitize --warnings-as-errors --output /tmp/proc-asan` for an isolated
AddressSanitizer/UndefinedBehaviorSanitizer build. Run its matching interpreter
with the compiler's sanitizer runtime preloaded before importing the module;
keep it separate from the normal installed extension. `--warnings-as-errors`
also works alone for a strict release compilation.

For complete child-process qualification, use the repository's isolated runner:

```console
python -m tests.tooling.runtime.proc_memory --sanitize --output /tmp/proc-memory
```

It builds the selected sources, preserves the sanitizer loader in fixture-owned
workers, proves native dispatch and retains test/source/binary receipts without
replacing production extensions. CI runs this qualification independently of
the other native modules' sharded sanitizer suite.
