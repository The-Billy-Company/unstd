# Regex internals

`profile.py` proves whether native matching preserves Python syntax and
semantics. `patterns.py` owns compiled-pattern adapters, Unicode recovery and
the strict linear-operation policy. Public compilation, backend selection and
caches stay in `unstd.rex`. `_faithful.py` preserves historical private imports
and class identities. These modules are not user APIs.
