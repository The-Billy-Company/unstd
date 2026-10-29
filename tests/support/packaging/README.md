# Artifact and consumer support

[`artifacts.py`](artifacts.py) freezes the working source tree, builds wheel and
source archives offline, rebuilds from the source archive, and creates pristine
consumer interpreters. Build and consumer processes have explicit deadlines.

[`public_api.json`](public_api.json) records the compatibility contract used by
[public API gates](../../suites/assurance/gates/packaging/test_public_api.py).
Versioned exports record interpreter additions; stdlib aliases are checked by
identity from their declared interpreter version, while earlier runtimes retain
the original owned signatures. Wrapper mutation canaries protect those aliases.
Archive contents, licenses, typing, optional extras, and installed behavior are
asserted in the [packaging plane](../../suites/assurance/planes/packaging/README.md).

[`surfaces.py`](surfaces.py) assigns optional split modules to installed-extra
behavior probes. The completeness check includes these explicit assignments,
and each required-extra consumer must load its assigned modules from its own
installation after exercising the real public behavior.
