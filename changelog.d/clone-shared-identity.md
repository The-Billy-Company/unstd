`clone.deep` now preserves shared-reference identity throughout each call,
including aliases crossing builtin containers, dataclasses, models and custom
`__deepcopy__` implementations. Repeated references to one source object reach
one cloned object, keeping graph relationships intact while avoiding duplicate
work and allocations. The native builtin path uses the same identity memo as
the Python walker. Separate calls still produce independent clones.

Native builtin graphs now track identities in a pointer table and export them
to the Python memo only at boundaries that require it. This reduces the cost
of preserving identity on distinct trees while retaining aliases across custom
copy hooks and object state.

Plain dataclasses also use native reconstruction with the enclosing memo,
reducing Python calls for allocation and state copying. The native layout cache
uses weak class references and invalidates on class changes; custom allocators
and attribute access keep the Python recipe.

This changes the previous deliberate alias-duplication behavior. `clone.asdict`
continues to match `dataclasses.asdict`, including copying repeated structural
positions separately; the stdlib `copy` and `deepcopy` re-exports are unchanged.
