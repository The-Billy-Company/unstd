"""``python3 -m bench`` — measure the speedups the READMEs claim.

Four verbs, and the closed set the rest of the house uses: ``run`` measures and
prints, ``verify`` measures and exits non-zero on a case that fell through its
floor, ``update`` raises floors that genuinely improved, and ``list`` says what
is registered without running anything.
"""

from __future__ import annotations

import argparse
import sys

from bench import cases as case_mod
from bench import report


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description=__doc__)
    p.add_argument(
        "verb",
        nargs="?",
        default="run",
        choices=("run", "verify", "update", "list"),
    )
    p.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="NAME",
        help=f"narrow to a group ({', '.join(case_mod.groups())}); repeatable",
    )
    p.add_argument("--rounds", type=int, default=5, help="timed batches per callable")
    p.add_argument("--markdown", action="store_true", help="emit a README table")
    p.add_argument("--json", action="store_true", help="emit machine-readable results")
    p.add_argument(
        "--no-audit",
        action="store_true",
        help="skip the equivalence check (it costs one call per case)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected = case_mod.registry(args.group)
    if not selected:
        print(f"bench: no cases in {args.group}", file=sys.stderr)
        return 2

    if args.verb == "list":
        for c in selected:
            mark = "" if c.available else f"  (skipped: no {c.backend})"
            kind = "" if c.equivalent else "  [not like-for-like]"
            print(f"{c.group:<9} {c.name}{kind}{mark}")
        return 0

    if not args.no_audit and (bad := case_mod.audit(selected)):
        # Fail before printing a single number: a ratio between two functions
        # that disagree is not a measurement of anything.
        print("bench: the two sides of a like-for-like case disagree", file=sys.stderr)
        for d in bad:
            print(f"  {d.case}: {d.detail}", file=sys.stderr)
        return 2

    results = report.run(selected, rounds=args.rounds)

    if args.json:
        print(report.as_json(results))
    elif args.markdown:
        print(report.markdown(results))
    else:
        print(report.table(results))

    measured = [r for r in results if not r.skipped]
    skipped = len(results) - len(measured)

    if args.verb == "update":
        raised, held = report.write_baseline(results)
        print(f"\nbaseline: {raised} raised, {held} held → {report.BASELINE.name}")
        return 0

    failed = [r for r in measured if r.failed]
    if not args.json and not args.markdown:
        unpinned = sum(r.floor is None for r in measured)
        print(
            f"\n{len(measured)} measured · {skipped} skipped · {unpinned} unpinned · "
            f"{len(failed)} below floor"
        )
        if any(r.noisy for r in measured):
            print(
                "some cases were noisy — close other work and re-run before believing them"
            )

    if args.verb == "verify":
        if skipped:
            print(
                f"bench: {skipped} case(s) could not run — verify needs every backend "
                "installed (`uv sync --all-extras`)",
                file=sys.stderr,
            )
            return 2
        for r in failed:
            print(
                f"bench: {r.case.name} is {r.ratio:.2f}× but claims ≥ {r.floor:.2f}×",
                file=sys.stderr,
            )
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
