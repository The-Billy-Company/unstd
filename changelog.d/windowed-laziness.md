Fix `iters.windowed` with the default step reading `n - 1` items from its
input as soon as it was called. An abandoned window iterator no longer drains a
shared source, and a failing source now raises on the first pull instead of from
the `windowed(...)` call itself; strided and zero-width windows were already lazy.
