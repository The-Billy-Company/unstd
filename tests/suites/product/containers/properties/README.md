# Sorted and persistent container state machines

`test_sorted_machines.py` replays generated insert, delete, update, range,
index and bisect histories on `SortedList` (plain and keyed), `SortedDict` and
`SortedSet` against list/bisect, dict and set reference models. Keyed lists
order by key only; equal-key values may appear in any relative order. A key
whose comparison raises at a scheduled step, a raising key function and an
unhashable key must propagate with identity and leave the container equal to
its model and still usable.

`test_map_history.py` derives `Map` versions from any retained version through
`set`, `delete`, `update` and mutation contexts. Every retained version must
stay equal to the immutable dict snapshot taken when it was made. Colliding
hashes exercise collision nodes; a key whose equality raises leaves versions
and an open mutation unchanged and usable, and finished or aborted mutations
reject further writes.

Upstream `SortedSet.add`/`update` and the merge phase of a large
`SortedList.update` are not failure-atomic under a raising comparison. These
re-exported classes promise no more than upstream, so those paths are not
pinned here.

These machines require the `containers` extra; with `--require-extras` an
absent backend fails instead of skipping.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/containers/properties -q
```

See [the module test overview](../README.md) for related evidence.
