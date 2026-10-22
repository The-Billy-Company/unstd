"""Versioned fuzz inventory and authenticated, reviewed replay corpus."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parent
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TARGET_FIELDS = {
    "name",
    "backends",
    "requirements",
    "max_input_bytes",
    "minimum_seeds",
    "surface",
    "oracle",
    "input_version",
}
_SEED_FIELDS = {"file", "sha256", "size", "label", "kind", "origin"}


class RegistryError(ValueError):
    """Inventory or corpus drift makes execution evidence invalid."""


@dataclass(frozen=True)
class Target:
    """One executable target, its capability contract, and input budget."""

    name: str
    backends: tuple[str, ...]
    requirements: tuple[str, ...]
    max_input_bytes: int
    minimum_seeds: int
    surface: str
    oracle: str
    input_version: int

    @property
    def module(self) -> str:
        """The sole allowed target module for this identity."""
        return f"tests.tooling.fuzz.targets.{self.name}"


@dataclass(frozen=True)
class Seed:
    """Exact synthetic bytes and their reviewed provenance."""

    target: str
    file: str
    data: bytes
    sha256: str
    label: str
    kind: str
    origin: str

    @property
    def identity(self) -> str:
        """A stable replay identity independent of filesystem ordering."""
        return f"{self.target}/{self.file}"


def _fail(message: str) -> None:
    raise RegistryError(message)


def _directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        _fail(f"missing or symlinked corpus/target directory: {path}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _read(path: Path, *, toml: bool = False):
    if path.is_symlink() or not path.is_file():
        _fail(f"missing or symlinked manifest: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        return (
            tomllib.loads(raw)
            if toml
            else json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (OSError, ValueError, UnicodeError) as error:
        message = f"cannot read {path}: {error}"
        raise RegistryError(message) from error


def load_registry(root: Path = ROOT) -> tuple[Target, ...]:
    """Validate target schema, identities, source presence, and discovery parity."""
    doc = _read(root / "registry.toml", toml=True)
    if (
        not isinstance(doc, dict)
        or set(doc) != {"version", "target"}
        or type(doc["version"]) is not int
        or doc["version"] != 1
    ):
        _fail("fuzz registry requires version=1 and [[target]] records")
    if not isinstance(doc["target"], list) or not doc["target"]:
        _fail("fuzz target inventory must not be empty")
    _directory(root / "targets")
    targets, names = [], set()
    for row in doc["target"]:
        if not isinstance(row, dict) or set(row) != _TARGET_FIELDS:
            _fail("each fuzz target must declare its exact schema")
        name = row["name"]
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            _fail(f"invalid or duplicate fuzz identity: {name!r}")
        names.add(name)
        for field in ("surface", "oracle"):
            if not isinstance(row[field], str) or not row[field].strip():
                _fail(f"{name}: {field} must describe a real contract")
        modes = row["backends"]
        if (
            not isinstance(modes, list)
            or not modes
            or any(not isinstance(mode, str) for mode in modes)
            or len(set(modes)) != len(modes)
            or not set(modes) <= {"installed", "stdlib"}
        ):
            _fail(f"{name}: invalid backend modes")
        imports = row["requirements"]
        if (
            not isinstance(imports, list)
            or not imports
            or any(
                not isinstance(module, str) or not _MODULE.fullmatch(module)
                for module in imports
            )
            or len(set(imports)) != len(imports)
        ):
            _fail(f"{name}: installed imports must be unique explicit module names")
        if (
            type(row["max_input_bytes"]) is not int
            or not 1 <= row["max_input_bytes"] <= 65536
        ):
            _fail(f"{name}: input budget must be within 1..65536 bytes")
        if type(row["minimum_seeds"]) is not int or row["minimum_seeds"] < 12:
            _fail(f"{name}: reviewed corpus floor is at least 12 seeds")
        if type(row["input_version"]) is not int or row["input_version"] != 1:
            _fail(f"{name}: unsupported input envelope version")
        source = root / "targets" / f"{name}.py"
        if source.is_symlink() or not source.is_file():
            _fail(f"{name}: missing or symlinked target source")
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError) as error:
            message = f"{name}: target is not readable valid Python"
            raise RegistryError(message) from error
        funcs = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "fuzz"
        ]
        if len(funcs) != 1:
            _fail(f"{name}: target must expose fuzz(data)")
        args = funcs[0].args
        if (
            len(args.posonlyargs) + len(args.args) != 1
            or args.defaults
            or args.vararg
            or args.kwarg
            or args.kwonlyargs
        ):
            _fail(
                f"{name}: target must expose fuzz(data) with exactly one required argument"
            )
        targets.append(
            Target(
                name,
                tuple(modes),
                tuple(imports),
                row["max_input_bytes"],
                row["minimum_seeds"],
                row["surface"],
                row["oracle"],
                row["input_version"],
            )
        )
    found = {
        path.stem
        for path in (root / "targets").glob("*.py")
        if not path.name.startswith("_")
    }
    if found != names:
        _fail(
            f"target discovery drift: files={sorted(found)}, inventory={sorted(names)}"
        )
    return tuple(sorted(targets, key=lambda item: item.name))


def load_seeds(target: Target, root: Path = ROOT) -> tuple[Seed, ...]:
    """Reject altered, orphaned, duplicate, oversized or underfed committed corpora."""
    _directory(root / "corpus")
    directory = root / "corpus" / target.name
    _directory(directory)
    doc = _read(directory / "seeds.json")
    if (
        not isinstance(doc, dict)
        or set(doc) != {"version", "target", "input_version", "seeds"}
        or type(doc["version"]) is not int
        or doc["version"] != 1
        or doc["target"] != target.name
        or type(doc["input_version"]) is not int
        or doc["input_version"] != target.input_version
    ):
        _fail(f"{target.name}: invalid corpus manifest")
    rows = doc["seeds"]
    if not isinstance(rows, list) or len(rows) < target.minimum_seeds:
        _fail(f"{target.name}: corpus below {target.minimum_seeds} reviewed seeds")
    seeds, files, digests = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _SEED_FIELDS:
            _fail(f"{target.name}: malformed seed entry")
        name = row["file"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.bin", name)
            or name in files
        ):
            _fail(f"{target.name}: unsafe or duplicate seed filename")
        files.add(name)
        if (
            type(row["size"]) is not int
            or not 0 <= row["size"] <= target.max_input_bytes
            or not isinstance(row["sha256"], str)
            or not _DIGEST.fullmatch(row["sha256"])
        ):
            _fail(f"{target.name}/{name}: invalid or oversized corpus digest/size")
        if (
            not isinstance(row["kind"], str)
            or row["kind"] not in {"valid", "invalid", "boundary"}
            or any(
                not isinstance(row[key], str) or not row[key].strip()
                for key in ("label", "origin")
            )
        ):
            _fail(f"{target.name}/{name}: missing reviewed provenance or kind")
        path = directory / name
        if path.is_symlink() or not path.is_file():
            _fail(f"{target.name}/{name}: missing or symlinked input")
        try:
            # Bound the read itself, even if a stale manifest understates size.
            with path.open("rb") as stream:
                data = stream.read(target.max_input_bytes + 1)
        except OSError as error:
            message = f"{target.name}/{name}: cannot read corpus input"
            raise RegistryError(message) from error
        digest = hashlib.sha256(data).hexdigest()
        if digest != row["sha256"] or len(data) != row["size"]:
            _fail(f"{target.name}/{name}: corpus digest/size mismatch")
        if len(data) > target.max_input_bytes or digest in digests:
            _fail(f"{target.name}/{name}: oversized or duplicate corpus bytes")
        digests.add(digest)
        seeds.append(
            Seed(
                target.name,
                name,
                data,
                digest,
                row["label"],
                row["kind"],
                row["origin"],
            )
        )
    actual = {
        path.name
        for path in directory.iterdir()
        if path.name != "seeds.json"
        and not (path.name == "README.md" and path.is_file() and not path.is_symlink())
    }
    if actual != files:
        _fail(
            f"{target.name}: orphan or missing corpus inputs: {sorted(actual ^ files)}"
        )
    if not {"valid", "invalid", "boundary"} <= {seed.kind for seed in seeds}:
        _fail(f"{target.name}: corpus requires valid, invalid and boundary controls")
    return tuple(sorted(seeds, key=lambda item: item.file))


def check(root: Path = ROOT) -> tuple[Target, ...]:
    """Validate all executable targets and every reviewed input before execution."""
    targets = load_registry(root)
    for target in targets:
        load_seeds(target, root)
    actual = {
        path.name
        for path in (root / "corpus").iterdir()
        if not (path.name == "README.md" and path.is_file() and not path.is_symlink())
    }
    if actual != {target.name for target in targets}:
        _fail("orphan or missing target corpus directories")
    return targets


def corpus_digest(seeds: tuple[Seed, ...]) -> str:
    """Hash identities and actual bytes in stable order, preserving attribution."""
    value = hashlib.sha256()
    for seed in sorted(seeds, key=lambda item: item.identity):
        value.update(seed.identity.encode())
        value.update(b"\0")
        value.update(hashlib.sha256(seed.data).digest())
    return value.hexdigest()
