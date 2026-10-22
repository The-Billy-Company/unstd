- Verification workers now distinguish macOS's zombie-only process-group
  `EPERM` race from real cleanup permission failures using bounded OS evidence.
  Live, foreign, or uncertain process membership still fails cleanup, and direct
  children are reaped even when group cleanup fails. Resource probes, Python
  workers, fuzz workers and chaos campaigns share this cleanup rule.
