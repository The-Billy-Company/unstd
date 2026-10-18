"""Run or replay the explicit concurrency lane with hard worker deadlines."""

from tests.tooling.chaos.__main__ import main as campaign_main


def main(argv=None):
    """Select concurrency groups while retaining shared campaign admission."""
    return campaign_main(argv, lane="concurrency")


if __name__ == "__main__":
    raise SystemExit(main())
