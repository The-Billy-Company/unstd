"""Rendering, and the ratchet that keeps a claim from quietly going stale.

The baseline pins a **floor** per case, not the measured ratio. A microbenchmark
that demands its own number back fails on any machine that is not the one that
recorded it, and a gate that cries wolf gets switched off within a week; a floor
set well under the observed ratio still catches the thing worth catching, which
is a backend silently stopping being fast (an accidental stdlib fallback, an
option bitmask that stopped applying, a dependency that regressed).

Floors only move one way. ``--update`` may raise a floor when the measured ratio
has genuinely improved, and refuses to lower one — lowering a floor to make a red
run go green is how a performance ratchet becomes a performance diary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import json
from pathlib import Path
import platform
import sys
from typing import TYPE_CHECKING

from bench.timing import Measurement, measure


if TYPE_CHECKING:
    from bench.cases import Case


BASELINE = Path(__file__).parent / "baseline.json"

# How much of an observed ratio a fresh floor claims. A case measured at 6.4×
# is pinned at 4.5×, which absorbs a slower CPU, a debug build of a backend, and
# a noisy CI runner without absorbing an actual regression.
#
# Note there is deliberately no clamp at 1.0. A case that measures *below*
# parity gets pinned below parity, because a floor is a statement about what was
# observed, and rounding an honest 0.57× up to "at least as fast as the stdlib"
# would make the baseline assert a win the code does not have — the ratchet would
# then fail on correct code forever, which is how a gate gets switched off.
_FLOOR_SHARE = 0.70


@dataclass(frozen=True, slots=True)
class Result:
    """One case's verdict."""

    case: Case
    accel: Measurement | None
    base: Measurement | None
    floor: float | None

    @property
    def skipped(self) -> bool:
        return self.accel is None or self.base is None

    @property
    def ratio(self) -> float:
        if self.accel is None or self.base is None or not self.accel.per_op_ns:
            return 0.0
        return self.base.per_op_ns / self.accel.per_op_ns

    @property
    def failed(self) -> bool:
        return not self.skipped and self.floor is not None and self.ratio < self.floor

    @property
    def noisy(self) -> bool:
        return not self.skipped and max(self.accel.spread, self.base.spread) > 0.25  # type: ignore[union-attr]


def load_baseline() -> dict[str, float]:
    if not BASELINE.is_file():
        return {}
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {k: float(v["floor"]) for k, v in raw.get("cases", {}).items()}


def run(cases: list[Case], *, rounds: int = 5) -> list[Result]:
    """Measure every available case against its stdlib twin."""
    floors = load_baseline()
    out: list[Result] = []
    for c in cases:
        floor = floors.get(c.name)
        if not c.available:
            out.append(Result(c, None, None, floor))
            continue
        # `partial` rather than a closure: the payload and both callables are
        # bound now, so nothing about which case is being timed can be read from
        # a loop variable that has already moved on.
        payload = c.payload()
        out.append(
            Result(
                c,
                measure(partial(c.accel, payload), rounds=rounds),
                measure(partial(c.base, payload), rounds=rounds),
                floor,
            )
        )
    return out


def write_baseline(results: list[Result]) -> tuple[int, int]:
    """Persist floors, raising only. Returns ``(raised, held)``.

    Cases this run did not measure (a ``--group`` narrowing) keep their rows as
    committed — a partial run may only speak for the cases it timed.
    """
    existing = load_baseline()
    cases: dict[str, dict[str, object]] = (
        json.loads(BASELINE.read_text(encoding="utf-8")).get("cases", {})
        if BASELINE.is_file()
        else {}
    )
    raised = held = 0
    for r in results:
        if r.skipped:
            if (prior := existing.get(r.case.name)) is not None:
                cases[r.case.name] = {"floor": prior, "measured": None}
                held += 1
            continue
        proposed = round(r.ratio * _FLOOR_SHARE, 2)
        prior = existing.get(r.case.name)
        floor = proposed if prior is None or proposed > prior else prior
        raised += floor != prior
        held += floor == prior
        cases[r.case.name] = {
            "floor": floor,
            "measured": round(r.ratio, 2),
            "group": r.case.group,
        }

    BASELINE.write_text(
        json.dumps(
            {
                "_": "Speed floors, ratio of stdlib cost to unstd cost. Raised by "
                "`python3 -m bench update`, never lowered by it. See bench/README.md.",
                "recorded": datetime.now(UTC).strftime("%Y-%m-%d"),
                "host": f"{platform.system()} {platform.machine()} "
                f"python{sys.version_info.major}.{sys.version_info.minor}",
                "cases": dict(sorted(cases.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return raised, held


def table(results: list[Result]) -> str:
    """The human view: one line per case, grouped, with the verdict last."""
    # Width follows the longest registered name rather than a guessed constant,
    # so adding a case can never silently shear the columns.
    w = max((len(r.case.name) for r in results), default=20) + 2
    lines = [
        f"  {'case':<{w}} {'unstd':>10} {'stdlib':>10} {'ratio':>9} {'floor':>9}  verdict",
        "-" * (w + 55),
    ]
    group = ""
    for r in results:
        if r.case.group != group:
            group = r.case.group
            lines.append(f"[{group}]")
        if r.skipped:
            dash = f"{'—':>10} {'—':>10} {'—':>9} {'—':>9}"
            lines.append(f"  {r.case.name:<{w}} {dash}  skipped — no {r.case.backend}")
            continue
        verdict = "FAIL" if r.failed else ("noisy" if r.noisy else "ok")
        floor = f"{r.floor:.2f}×" if r.floor is not None else "unpinned"
        lines.append(
            f"  {r.case.name:<{w}} {r.accel.human():>10} {r.base.human():>10} "  # type: ignore[union-attr]
            f"{r.ratio:>8.2f}× {floor:>9}  {verdict}"
        )
    return "\n".join(lines)


def markdown(results: list[Result]) -> str:
    """The README view — the shape the per-group tables want pasted in."""
    lines = [
        "| Case | `unstd` | stdlib | Speedup |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        if r.skipped:
            continue
        speed = f"**{r.ratio:.1f}×**" if r.ratio >= 2 else f"{r.ratio:.1f}×"
        lines.append(
            f"| {r.case.name} | {r.accel.human()} | {r.base.human()} | {speed} |"  # type: ignore[union-attr]
        )
    return "\n".join(lines)


def as_json(results: list[Result]) -> str:
    return json.dumps(
        {
            "host": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "cases": [
                {
                    "name": r.case.name,
                    "group": r.case.group,
                    "skipped": r.skipped,
                    "accel_ns": r.accel.per_op_ns if r.accel else None,
                    "base_ns": r.base.per_op_ns if r.base else None,
                    "ratio": round(r.ratio, 3) or None,
                    "floor": r.floor,
                    "failed": r.failed,
                    "equivalent": r.case.equivalent,
                }
                for r in results
            ],
        },
        indent=2,
    )
