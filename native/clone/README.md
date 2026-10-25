# Native structural cloning

[`src/adapter.c`](src/adapter.c) is the complete C implementation and CPython
binding for `unstd.clone._clone_native`; its filename is independent of the
installed module name. [`src/README.md`](src/README.md) maps its native entrypoints.

The [Python walker](../../src/unstd/clone/structural.py) admits copy protocols
and chooses graph-preserving `deep` or positional `asdict` semantics. Native
calls handle exact builtin containers and admitted plain dataclasses, then
return `NotImplemented` when the walker must take over. Proven builtin cycles
select `deep`'s whole-graph `deepcopy` fallback.

The [build hook](../../hatch_build.py) compiles this source for CPython GIL
builds. When changing admission or reconstruction, check both native paths and
[product tests](../../tests/suites/product/clone/README.md): GC/finalizers can
mutate a source during allocation, and a dataclass twin cannot be recreated
after observable state callbacks have begun.
