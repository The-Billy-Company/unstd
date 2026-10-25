# Private TOML implementation

`common.py` validates values and normalizes plain data to the common TOML
dialect. It loads only when the optional writing backend is available.
The public module retains stdlib read aliases, builders, and write entrypoints.
