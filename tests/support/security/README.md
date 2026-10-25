# Independent security fixtures

[`tls.py`](tls.py) creates throwaway certificate authorities and identities,
and runs bounded real loopback TLS peers. Certificates are issued independently
of the client policy under test; each exchange owns its listener, connection,
worker, timeout, and cleanup.

Handshake expectations belong in the
[TLS penetration suite](../../suites/assurance/security/pentest/README.md).
These helpers are test fixtures and are never imported by production modules.
