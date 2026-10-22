"""Deep and cyclic inputs under a fixed recursion limit, in deadline-owned workers.

Each worker lowers its own recursion limit so pytest's stack depth cannot change
the boundary. ``copy.deepcopy`` on the same value is the oracle: wherever it
succeeds, ``deep`` must succeed with an exact, independent chain, and the only
permitted failure is ``RecursionError``. Cycles must keep ``deepcopy``'s shape,
including the aliasing the documented whole-graph fallback preserves.
"""

from __future__ import annotations

import importlib.util
import json
from typing import TYPE_CHECKING

from tests.support.environment.backends import require_backend
from tests.support.environment.process import isolated_environment, run_python


if TYPE_CHECKING:
    from pathlib import Path


_NATIVE = importlib.util.find_spec("unstd.clone._clone_native") is not None

_PRELUDE = """
import copy, dataclasses, json, sys
from unstd.clone import structural as s
try:
    from unstd.clone._clone_native import clone as native
except ImportError:
    native = None

@dataclasses.dataclass
class Link:
    next: object

@dataclasses.dataclass(eq=False)
class Node:
    next: object = None

backends = [("python", None)] + ([("native", native)] if native else [])
"""

_DEPTH = (
    _PRELUDE
    + """
WRAP = {
    "list": lambda v: [v],
    "dict": lambda v: {"k": v},
    "tuple": lambda v: (v,),
    "frozenset": lambda v: frozenset([v]),
    "dataclass": Link,
    "mixed": lambda v: [{"k": (Link(v),)}],
}

def children(node):
    if type(node) is dict:
        return node["k"]
    if type(node) is Link:
        return node.next
    (child,) = node
    return child

def chain(node):
    out = []
    while type(node) is not int:
        out.append(node)
        node = children(node)
    return out

def unwrap(source, result):
    left, right = chain(source), chain(result)
    if len(left) != len(right):
        return "length"
    for a, b in zip(left, right):
        if type(a) is not type(b):
            return "type"
        if type(a) in (list, dict, Link) and a is b:
            return "shared"
    return "ok"

def attempt(fn, value, limit):
    sys.setrecursionlimit(limit)
    try:
        return "ok", fn(value)
    except RecursionError:
        return "recursion", None
    finally:
        sys.setrecursionlimit(10_000)

rows = []
for backend, clone in backends:
    s._native_clone = clone
    for shape, wrap in WRAP.items():
        for depth in range(0, 330, 15):
            value = 0
            for _ in range(depth):
                value = wrap(value)
            oracle, _ = attempt(copy.deepcopy, value, 250)
            outcome, result = attempt(s.deep, value, 250)
            rows.append([backend, shape, depth, oracle, outcome,
                         unwrap(value, result) if outcome == "ok" else None])
print(json.dumps(rows))
"""
)


def _run(code: str, tmp_path: Path) -> object:
    result = run_python(code, cwd=tmp_path, env=isolated_environment(), timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_depth_sweep_succeeds_wherever_deepcopy_does(tmp_path: Path) -> None:
    rows = _run(_DEPTH, tmp_path)
    backends = {row[0] for row in rows}
    assert backends == ({"python", "native"} if _NATIVE else {"python"})
    for backend, shape, depth, oracle, outcome, chain in rows:
        case = f"{backend}/{shape}/{depth}"
        assert outcome in {"ok", "recursion"}, case
        if oracle == "ok":
            assert outcome == "ok", f"{case}: deepcopy succeeded, deep did not"
        if outcome == "ok":
            assert chain == "ok", f"{case}: {chain}"
    oracles = {(row[1], row[3]) for row in rows}
    for shape in ("list", "dict", "tuple", "frozenset", "dataclass", "mixed"):
        assert (shape, "ok") in oracles, f"{shape}: no positive control"
        assert (shape, "recursion") in oracles, f"{shape}: limit never reached"


_CYCLES = (
    _PRELUDE
    + """
def list_cycle():
    a = [1]
    a.append(a)
    return a, lambda r, src: [r is not src, r[1] is r, r[0] == 1]

def dict_cycle():
    d = {"n": [2]}
    d["self"] = d
    return d, lambda r, src: [r is not src, r["self"] is r, r["n"] == [2],
                              r["n"] is not src["n"]]

def dataclass_cycle():
    link = Link(None)
    link.next = [link]
    return link, lambda r, src: [r is not src, r.next[0] is r, r.next is not src.next]

def set_cycle():
    node = Node()
    node.next = {node}
    return node, lambda r, src: [r is not src, next(iter(r.next)) is r,
                                 r.next is not src.next]

def aliased_cycle():
    shared = [3]
    g = {"a": shared, "b": shared}
    g["self"] = g
    return g, lambda r, src: [r["self"] is r, r["a"] is r["b"], r["a"] is not shared]

CASES = [list_cycle, dict_cycle, dataclass_cycle, set_cycle, aliased_cycle]
EXTRA

sys.setrecursionlimit(300)
out = {}
for backend, clone in backends:
    s._native_clone = clone
    for case in CASES:
        source, facts = case()
        out[f"{backend}/{case.__name__}"] = [facts(copy.deepcopy(source), source),
                                            facts(s.deep(source), source)]
print(json.dumps(out))
"""
)

_PYDANTIC_CYCLE = """
import pydantic

class Tree(pydantic.BaseModel):
    kids: list = []

# BaseModel.__deepcopy__ memoizes the copied __dict__ but not the model itself,
# so the cycle re-enters through one extra model that shares the copied list.
def model_cycle():
    tree = Tree()
    tree.kids.append(tree)
    return tree, lambda r, src: [type(r) is Tree, r is not src, r.kids is not src.kids,
                                 r.kids[0] is not src, r.kids[0].kids is r.kids]

CASES.append(model_cycle)
"""


def _assert_cycles(out: dict[str, list[list[bool]]], cases: int) -> None:
    assert len(out) == cases * (2 if _NATIVE else 1)
    for case, (oracle, observed) in out.items():
        assert all(oracle), f"{case}: the deepcopy oracle disagrees with the fixture"
        assert observed == oracle, case


def test_cycles_keep_deepcopys_shape_and_aliases(tmp_path: Path) -> None:
    _assert_cycles(_run(_CYCLES.replace("EXTRA", ""), tmp_path), 5)


def test_pydantic_model_cycle_falls_back_whole(tmp_path: Path) -> None:
    require_backend("pydantic")
    _assert_cycles(_run(_CYCLES.replace("EXTRA", _PYDANTIC_CYCLE), tmp_path), 6)
