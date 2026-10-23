# Artifact and consumer support

[`artifacts.py`](artifacts.py) freezes the working source tree, builds wheel and
source archives offline, rebuilds from the source archive, and creates pristine
consumer interpreters. Build and consumer processes have explicit deadlines.

[`public_api.json`](public_api.json) records the compatibility contract used by
[public API gates](../../suites/assurance/gates/packaging/test_public_api.py).
Archive contents, licenses, typing, optional extras, and installed behavior are
asserted in the [packaging plane](../../suites/assurance/planes/packaging/README.md).
