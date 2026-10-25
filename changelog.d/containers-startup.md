Accelerate sorted collection operations with optional bundled CPython kernels
and compatible subclasses of the upstream types. Retain the original classes
when native acceleration is unavailable or the backend version is unqualified;
Map keeps its existing native backend. Load each container family on demand,
preserving missing-extra errors. Add a paired startup/operation profile with
Linux and macOS evidence and include containers in native wheel/sanitizer checks.
