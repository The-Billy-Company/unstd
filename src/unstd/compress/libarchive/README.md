---
doc_radar:
  sentinels:
    - file: src/unstd/compress/libarchive/__init__.py
      contains: ['_import_module("libarchive")', '_iter_modules(_backend.__path__)', '_sys.modules[__name__] = _backend']
    - file: src/unstd/compress/libarchive/read.pyi
      contains: ['class ArchiveRead:', 'def new_archive_read(']
    - file: tests/suites/product/compress/unit/test_libarchive_port.py
      contains: ['def test_archive_package_and_qualified_children_are_original_singletons(', 'def test_existing_warning_and_error_callback_identity_is_preserved(']
---

# Native archive module ports

We expose the original [libarchive-c](https://github.com/Changaco/python-libarchive-c)
package through `unstd[archives]`. A loadable native libarchive library must also
be available on the host. Importing `unstd.compress` loads neither the binding
nor the native library; requesting this namespace reports a clear `ImportError`
when either is unavailable.

```python
from unstd.compress import libarchive
from unstd.compress.libarchive import ffi
from unstd.compress.libarchive.read import ArchiveRead, new_archive_read
from unstd.compress.libarchive.exception import ArchiveError
```

These names are the provider's actual module, functions, classes and ctypes
bindings. Qualified child imports resolve to those same loaded modules, so FFI
callbacks and native diagnostic state are never duplicated. The complete runtime
API remains available, including original readers, writers, filters, extraction
helpers and constants. Native format and filter support depends on the host
library.

The declarations cover the observed libarchive-c 5.3 reader, entry metadata,
error and writer interfaces. Other upstream members retain their runtime API
and dynamic typing. Consume each entry's blocks before advancing the iterator,
and keep native handles inside their owning read/write contexts. Type
annotations cannot enforce those native lifetimes.

This namespace preserves upstream behavior. Applications still own cumulative
expansion, entry and nesting limits; path/link/encryption policy; and inspection
of native warnings and checksums before publishing a complete result. We do not
copy an application's archive policy into the codec binding.
