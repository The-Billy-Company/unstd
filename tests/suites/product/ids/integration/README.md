# UUID process boundaries

`test_process_boundaries.py` imports `unstd.ids.uid` in fresh interpreters to observe import-time backend selection: `uuid_utils` when the `ids` extra is installed, then stdlib `uuid.uuid7`, then the hand-rolled fallback. It also forks three `multiprocessing` children while the forking thread holds a saturated counter lock. Each child must start from an unlocked, unseeded counter and mint the exact RFC 9562 values its own entropy determines, and the parent's state must stay intact.

The installed-backend case requires the `ids` extra and fails rather than skips under `--require-extras`. The fork case requires POSIX `fork`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/ids/integration -q
```

See [the module test overview](../README.md) for related evidence.
