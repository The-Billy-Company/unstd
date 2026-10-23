# Clone kernel source

[`_clone_native.c`](_clone_native.c) handles eligible exact-type builtin trees.
Unsupported values and cycles return to the Python wrapper, which owns the full
copy protocol. The build hook compiles this source into the existing private
`unstd.clone._clone_native` module.
