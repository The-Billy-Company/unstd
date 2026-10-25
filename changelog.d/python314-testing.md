Development now uses Python 3.14.9. Default CI, platform contracts, dependency
floors, base and partial-extra installs, sanitizer checks, and scheduled chaos
and concurrency campaigns run on Python 3.14 to match the consuming application.
Deterministic fuzzing also covers 3.14 while retaining Python 3.12 for Atheris.
The existing Python 3.12–3.14 compatibility matrices remain required.
