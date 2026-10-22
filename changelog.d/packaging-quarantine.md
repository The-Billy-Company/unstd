### Fixed

- Isolated per-extra consumers preserve the frozen manifest's complete resolver
  quarantine policy, including package exceptions, fixing locked exports after
  policy changes without rewriting the lockfile.
