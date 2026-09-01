# `bench` — the harness behind the speedup claims

`unstd` exists because a native backend is faster than the stdlib module it
stands in for. That is a measurable claim. Until this package landed it was an
unmeasured one: the `clone` README carried a table of microseconds annotated
"measured on this dev env" that nobody, including its author, could run again.

```console
python3 -m bench                 # measure everything, print a table
python3 -m bench --group serde   # one group
python3 -m bench verify          # exit non-zero if a claim fell through its floor
python3 -m bench update          # raise floors that genuinely improved
python3 -m bench --markdown      # the table shape a README wants pasted in
python3 -m bench list            # what is registered, without running it
```

Every backend has to be installed for a full run: `uv sync --all-extras`.

## What a number means

Each case is one accelerated `unstd` call and the stdlib call it stands in for,
over one payload. The headline is the **ratio** — stdlib cost divided by `unstd`
cost — not the absolute microseconds, which are a fact about whichever laptop
produced them.

`unstd` and `stdlib` columns are the least-contaminated per-call cost: the
minimum of several timed batches, with GC disabled for the duration. Noise on a
shared machine is one-sided (a preemption or a downclock can only make a sample
slower), so the fastest batch observed is the one least polluted by work that was
not the code under test. A case whose fastest and slowest batch differ by more
than 25% is flagged `noisy` — close your other work and run it again before
believing it.

Batch size is calibrated per callable rather than fixed. The cases here span four
orders of magnitude, and a 190 ns fuzzy-ratio and a 400 ms backtracking search
cannot share an iteration count without one of them drowning in loop overhead.

## Why the baseline pins floors, not values

`bench/baseline.json` holds a **floor** per case, set at 70% of the ratio that
was observed when it was recorded. A gate that demanded its own number back would
fail on every machine except the one that recorded it, and a gate that cries wolf
gets switched off within a week. A floor well under the observed ratio still
catches the thing worth catching: a backend that silently stopped being fast — an
accidental stdlib fallback, an option bitmask that stopped applying, a dependency
that regressed.

Floors move one way. `update` raises a floor when the measured ratio genuinely
improved and refuses to lower one, because lowering a floor to turn a red run
green is how a performance ratchet becomes a performance diary.

There is deliberately **no clamp at 1.0**. A case that measures below parity gets
pinned below parity — see the `pack` finding below. A floor is a statement about
what was observed, and rounding an honest 0.56× up to "at least as fast as the
stdlib" would make the baseline assert a win the code does not have, and then
fail on correct code forever.

## Two things the harness refuses to do

**Grade a missing backend as a pass.** A case names the backend its accelerated
path needs. Without it, `unstd` has already fallen back to the stdlib, both sides
of the comparison are the *same function*, and the 1.0× that comes out means
nothing. Those cases print as `skipped`, and `verify` exits 2 rather than 0 if
any case skipped — a green run with half the backends absent is worse than no run.

`rex` needs more than an import check, so it gets a custom probe: it degrades to
stdlib `re` per *pattern*, not per install, so the only honest question is what
`rex.compile` returned for this pattern. That probe also survives the backend
being swapped underneath the harness, which happened once already.

**Report a ratio between two functions that disagree.** Before any measurement,
every case marked like-for-like has both sides called once and their answers
compared; a mismatch aborts the run with the diff. One wrong `separators`
argument or one mismatched dtype turns a rigorous ratio into a fabricated one,
and it is the easiest mistake in the genre.

Cases where the two sides *legitimately* compute different things are marked
`[not like-for-like]` by `bench list` and carry a note saying so. RE2 versus a
backtracking engine, BLAKE3 versus BLAKE2b, and a PCG64 bulk draw versus a
`random.randrange` loop are capability comparisons, not faster spellings of one
algorithm, and the note has to say which.

## Findings worth knowing

The harness earned its keep on the first run:

- **`pack_array` from a Python list is slower than `struct.pack`** (0.56×). This
  is structural, not a bug. `struct.pack` with a repeat-count format unboxes
  50 000 Python floats in a tight C loop writing straight into the output buffer;
  NumPy has to materialize an ndarray first. Nothing beats it at that job —
  `array.array` and `np.fromiter` were both measured and both lose too. The seam's
  real value is the shape beside it: handed an ndarray it already owns,
  `pack_array` is a memcpy and wins **159×**, and `unpack_array` returns a
  zero-copy view for **1667×**. Hold your data in an array, not a list.
- **`hash.sum_`'s honest twin is `blake2b`, not `sha256`.** BLAKE2 is what
  `hashlib` ships from the BLAKE family, so it is the thing `sum_` stands in for,
  and BLAKE3 beats it 1.7×. `sha256` is not a fair comparison in either
  direction: every ARMv8 and modern x86 core implements it in silicon, so
  `hashlib.sha256` is a hardware instruction and beats single-threaded BLAKE3
  (350 µs vs 453 µs on an M-series laptop). That is a fact about the chip, not
  the algorithm, and it is not something a caller can act on.
- **`clone.deep` on a small dict is 1.19×, not the 1.8× the README claimed.**
  The floor case is real and the README now says what the harness measures.

## Adding a case

One `case(...)` call in `bench/cases.py`, then `python3 -m bench update` to pin
its floor. Bind the callables at module import — a `__import__` or attribute walk
inside the measured lambda adds a few hundred nanoseconds per iteration, which is
invisible on the 1 MiB hash case and roughly the entire measurement on the small
dict clone.

Mark `equivalent=False` if the two sides do not compute the same answer, and say
why in `note`. If importability does not prove the fast path was taken, pass a
`probe`.
