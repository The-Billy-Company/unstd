"""Offline dependency trust checks and deterministic lockfile SBOM export.

This checks committed resolution inputs without importing project dependencies,
running a resolver, or contacting an index. Resolver freshness and advisory
lookups are separate checks: a syntactically valid hash is not proof that an
artifact is benign or that its bytes were downloaded and verified.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from tests.support.anchors import REPO_ROOT


if TYPE_CHECKING:
    from collections.abc import Sequence


_PYPI = "https://pypi.org/simple"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
_REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s|\[|[<>=!~;(]|$)")


class SupplyChainError(ValueError):
    """A dependency input crosses an unapproved trust boundary."""


@dataclass(frozen=True)
class _Package:
    ecosystem: str
    name: str
    version: str
    lockfile: str
    hashes: tuple[str, ...]
    dependencies: tuple[str, ...]
    external: bool

    @property
    def purl(self) -> str:
        return f"pkg:{self.ecosystem}/{quote(self.name, safe='')}@{quote(self.version, safe='')}"


def _fail(message: str) -> None:
    raise SupplyChainError(message)


def _table(value: object, context: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{context}: expected a TOML table")
    return value


def _rows(value: object, context: str) -> list:
    if not isinstance(value, list):
        _fail(f"{context}: expected a list")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{context}: expected a nonempty string without surrounding spaces")
    return value


def _name(value: object, context: str) -> str:
    name = _string(value, context)
    if not _NAME.fullmatch(name):
        _fail(f"{context}: invalid package name {name!r}")
    return re.sub(r"[-_.]+", "-", name).lower()


def _read(root: Path, path: Path) -> dict:
    if not path.resolve().is_relative_to(root):
        _fail(f"{path}: dependency input escapes the repository")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read {path.relative_to(root)}: {exc}"
        raise SupplyChainError(message) from exc


def _requirement(value: object, context: str) -> str:
    text = _string(value, context)
    requirement = text.partition(";")[0]
    match = _REQUIREMENT.match(requirement)
    if match is None or "@" in requirement or "://" in requirement:
        _fail(f"{context}: dependencies must use registry names, not URLs or paths")
    return _name(match[1], context)


def _requirements(value: object, context: str) -> set[str]:
    return {_requirement(row, context) for row in _rows(value, context)}


def _manifest(root: Path) -> tuple[dict, dict, dict]:
    if (root / "uv.toml").exists():
        _fail("uv.toml: standalone resolver overrides require policy review")
    data = _read(root, root / "pyproject.toml")
    project = _table(data.get("project"), "pyproject.toml project")
    uv = _table(_table(data.get("tool"), "pyproject.toml tool").get("uv"), "tool.uv")
    if uv.get("exclude-newer") != "P2D":
        _fail("tool.uv.exclude-newer: preserve the P2D release quarantine")
    if uv.get("exclude-newer-package", {}) != {"irregex": False, "certifi": "P1D"}:
        _fail(
            "tool.uv.exclude-newer-package: only the reviewed irregex and certifi exceptions are allowed"
        )
    for key in (
        "sources",
        "index",
        "index-url",
        "extra-index-url",
        "default-index",
        "find-links",
        "allow-insecure-host",
        "override-dependencies",
        "build-constraint-dependencies",
    ):
        if uv.get(key):
            _fail(f"tool.uv.{key}: dependency source overrides require policy review")
    _requirements(project.get("dependencies", []), "project.dependencies")
    extras = _table(
        project.get("optional-dependencies", {}), "project.optional-dependencies"
    )
    for extra, rows in extras.items():
        _requirements(rows, f"project.optional-dependencies.{extra}")
    groups = _table(data.get("dependency-groups", {}), "dependency-groups")
    for group, rows in groups.items():
        _requirements(rows, f"dependency-groups.{group}")
    build = _table(data.get("build-system"), "build-system")
    build_requires = _rows(build.get("requires"), "build-system.requires")
    _requirements(build_requires, "build-system.requires")
    build_group = _rows(groups.get("build"), "dependency-groups.build")
    if not build_requires or set(build_requires) != set(build_group):
        _fail(
            "build-system.requires must exactly match the audited dependency-groups.build requirements"
        )
    return project, uv, groups


def _artifact(value: object, context: str, *, wheel: bool) -> str:
    artifact = _table(value, context)
    url = _string(artifact.get("url"), f"{context}.url")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.netloc == "files.pythonhosted.org"
            and parsed.path.startswith("/packages/")
            and not parsed.query
            and not parsed.fragment
            and "\\" not in url
            and not any(ord(char) < 33 for char in url)
        )
    except ValueError:
        valid = False
    if not valid:
        _fail(f"{context}.url: artifacts must use HTTPS on files.pythonhosted.org")
    if wheel and not parsed.path.endswith(".whl"):
        _fail(f"{context}.url: wheel artifact must end in .whl")
    digest = _string(artifact.get("hash"), f"{context}.hash")
    if not digest.startswith("sha256:") or not _HASH.fullmatch(digest[7:]):
        _fail(f"{context}.hash: expected a complete lowercase SHA256 digest")
    if type(artifact.get("size")) is not int or artifact["size"] <= 0:
        _fail(f"{context}.size: expected a positive integer")
    return digest[7:]


def _edges(package: dict, context: str) -> tuple[str, ...]:
    edges = list(_rows(package.get("dependencies", []), f"{context}.dependencies"))
    for key in ("optional-dependencies", "dev-dependencies"):
        for group in _table(package.get(key, {}), f"{context}.{key}").values():
            edges.extend(_rows(group, f"{context}.{key}"))
    names: list[str] = []
    for edge in edges:
        row = _table(edge, f"{context} dependency")
        names.append(_name(row.get("name"), f"{context} dependency.name"))
        if "source" in row and row["source"] != {"registry": _PYPI}:
            _fail(f"{context}: dependency edge contains an unapproved source")
    return tuple(sorted(set(names)))


def _python(root: Path) -> tuple[list[_Package], int]:
    project, uv, groups = _manifest(root)
    lock = _read(root, root / "uv.lock")
    if type(lock.get("version")) is not int or lock["version"] != 1:
        _fail("uv.lock: unsupported lockfile version")
    if lock.get("requires-python") != project.get("requires-python"):
        _fail("uv.lock requires-python differs from pyproject.toml")
    options = _table(lock.get("options"), "uv.lock options")
    if options.get("exclude-newer-span") != uv["exclude-newer"]:
        _fail("uv.lock options: release quarantine differs from pyproject.toml")
    expected_exceptions = {
        name: {"timestamp": "0001-01-01T00:00:00Z", "span": value}
        if isinstance(value, str)
        else value
        for name, value in uv["exclude-newer-package"].items()
    }
    if options.get("exclude-newer-package", {}) != expected_exceptions:
        _fail("uv.lock options: quarantine exemptions differ from pyproject.toml")
    project_name = _name(project.get("name"), "project.name")
    packages: list[_Package] = []
    identities: set[tuple[str, str]] = set()
    root_rows: list[dict] = []
    artifact_count = 0
    for value in _rows(lock.get("package"), "uv.lock package"):
        row = _table(value, "uv.lock package")
        name = _name(row.get("name"), "uv.lock package.name")
        version = _string(row.get("version"), f"uv.lock {name}.version")
        context = f"uv.lock {name}@{version}"
        identity = (name, version)
        if identity in identities:
            _fail(f"{context}: duplicate locked package")
        identities.add(identity)
        source = _table(row.get("source"), f"{context}.source")
        external = source == {"registry": _PYPI}
        if not external:
            if source != {"editable": "."} or name != project_name:
                _fail(
                    f"{context}.source: only PyPI and the editable project root are allowed"
                )
            if version != project.get("version"):
                _fail(f"{context}: root version differs from pyproject.toml")
            root_rows.append(row)
        hashes: list[str] = []
        if "sdist" in row:
            hashes.append(_artifact(row["sdist"], f"{context}.sdist", wheel=False))
        hashes.extend(
            _artifact(wheel, f"{context}.wheel", wheel=True)
            for wheel in _rows(row.get("wheels", []), f"{context}.wheels")
        )
        if external and not hashes:
            _fail(f"{context}: registry package has no hashed artifacts")
        if not external and hashes:
            _fail(f"{context}: editable root cannot carry remote artifacts")
        artifact_count += len(hashes)
        packages.append(
            _Package(
                "pypi",
                name,
                version,
                "uv.lock",
                tuple(sorted(set(hashes))),
                _edges(row, context),
                external,
            )
        )
    if len(root_rows) != 1:
        _fail("uv.lock: expected exactly one editable project root")
    known_names = {package.name for package in packages}
    for package in packages:
        missing = set(package.dependencies) - known_names
        if missing:
            _fail(
                f"uv.lock {package.name}: unresolved dependencies: {', '.join(sorted(missing))}"
            )
    project_root = root_rows[0]
    if set(_edges(project_root, "uv.lock project")) != (
        _requirements(project.get("dependencies", []), "project.dependencies")
        | set().union(
            *(
                _requirements(rows, "project.optional-dependencies")
                for rows in project.get("optional-dependencies", {}).values()
            )
        )
        | set().union(
            *(_requirements(rows, "dependency-groups") for rows in groups.values())
        )
    ):
        _fail("uv.lock project: direct dependency names differ from pyproject.toml")
    return packages, artifact_count


def _cargo(root: Path) -> list[_Package]:
    manifests = sorted((root / "native").rglob("Cargo.toml"))
    for lock in (root / "native").rglob("Cargo.lock"):
        if not lock.with_name("Cargo.toml").is_file():
            _fail(f"{lock.relative_to(root)}: native crate manifest is missing")
    directories = {root}
    for manifest in manifests:
        directories.update(
            parent for parent in manifest.parents if parent.is_relative_to(root)
        )
    for directory in sorted(directories):
        for filename in ("config", "config.toml"):
            path = directory / ".cargo" / filename
            if path.exists():
                config = _read(root, path)
                if (
                    config.get("source")
                    or config.get("registries")
                    or config.get("registry")
                ):
                    _fail(
                        f"{path.relative_to(root)}: Cargo source overrides require policy review"
                    )
    packages: list[_Package] = []
    for manifest_path in manifests:
        manifest = _read(root, manifest_path)
        context = str(manifest_path.relative_to(root))
        package = _table(manifest.get("package"), f"{context} package")
        local_name = _string(package.get("name"), f"{context} package.name")
        local_version = _string(package.get("version"), f"{context} package.version")
        if manifest.get("patch") or manifest.get("replace"):
            _fail(f"{context}: Cargo patch/replace overrides require policy review")
        sections = [manifest]
        sections.extend(
            _table(manifest.get("target", {}), f"{context} target").values()
        )
        for section in sections:
            for key in ("dependencies", "build-dependencies", "dev-dependencies"):
                dependencies = _table(
                    _table(section, context).get(key, {}), f"{context} {key}"
                )
                if dependencies:
                    _fail(
                        f"{context} {key}: external Rust dependencies require an audited lane before admission"
                    )
        lock_path = manifest_path.with_name("Cargo.lock")
        lockfile = str(lock_path.relative_to(root))
        lock = _read(root, lock_path)
        if type(lock.get("version")) is not int or lock["version"] not in {3, 4}:
            _fail(f"{lockfile}: unsupported Cargo lockfile version")
        seen: set[tuple[str, str]] = set()
        local_count = 0
        for value in _rows(lock.get("package"), f"{lockfile} package"):
            row = _table(value, f"{lockfile} package")
            name = _string(row.get("name"), f"{lockfile} package.name")
            version = _string(row.get("version"), f"{lockfile} {name}.version")
            identity = (name, version)
            if identity in seen:
                _fail(f"{lockfile} {name}: duplicate locked package")
            seen.add(identity)
            source = row.get("source")
            external = source is not None
            hashes: tuple[str, ...] = ()
            if external:
                _fail(
                    f"{lockfile} {name}: external Rust dependencies require an audited lane before admission"
                )
            elif identity != (local_name, local_version):
                _fail(f"{lockfile} {name}: unapproved local package")
            else:
                local_count += 1
            edges = tuple(
                _string(edge, f"{lockfile} {name}.dependencies").split()[0]
                for edge in _rows(
                    row.get("dependencies", []), f"{lockfile} {name}.dependencies"
                )
            )
            packages.append(
                _Package("cargo", name, version, lockfile, hashes, edges, external)
            )
        if local_count != 1:
            _fail(f"{lockfile}: expected exactly one local manifest package")
        names = {name for name, _ in seen}
        for package in (row for row in packages if row.lockfile == lockfile):
            if set(package.dependencies) - names:
                _fail(f"{lockfile} {package.name}: unresolved Cargo dependency")
    return packages


def _inventory(root: Path) -> tuple[list[_Package], dict[str, int]]:
    root = root.resolve()
    python, artifacts = _python(root)
    cargo = _cargo(root)
    return python + cargo, {
        "python_packages": len(python),
        "python_artifacts": artifacts,
        "rust_packages": len(cargo),
        "rust_external_packages": sum(package.external for package in cargo),
    }


def check_policy(repo_root: Path) -> dict[str, int]:
    """Validate committed dependency trust inputs without network or execution."""
    return _inventory(repo_root)[1]


def build_sbom(repo_root: Path) -> dict:
    """Build deterministic CycloneDX 1.6 inventory for all locked environments."""
    packages, _ = _inventory(repo_root)
    selected = packages
    components: dict[str, dict] = {}
    dependencies: dict[str, set[str]] = {}
    for package in sorted(selected, key=lambda row: (row.purl, row.lockfile)):
        component = components.setdefault(
            package.purl,
            {
                "type": "library",
                "bom-ref": package.purl,
                "name": package.name,
                "version": package.version,
                "purl": package.purl,
                "hashes": [],
                "properties": [],
            },
        )
        hashes = {row["content"] for row in component["hashes"]} | set(package.hashes)
        component["hashes"] = [
            {"alg": "SHA-256", "content": digest} for digest in sorted(hashes)
        ]
        prop = {"name": "unstd:lockfile", "value": package.lockfile}
        if prop not in component["properties"]:
            component["properties"].append(prop)
        edges = dependencies.setdefault(package.purl, set())
        edges.update(
            candidate.purl
            for candidate in selected
            if candidate.ecosystem == package.ecosystem
            and candidate.name in package.dependencies
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": list(components.values()),
        "dependencies": [
            {"ref": ref, "dependsOn": sorted(edges)}
            for ref, edges in sorted(dependencies.items())
        ],
    }


def validate_audit(path: Path) -> dict[str, int]:
    """Reject incomplete, inconsistent or non-clean uv audit JSON evidence."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = f"cannot read audit report {path}: {exc}"
        raise SupplyChainError(message) from exc
    data = _table(report, "audit report")
    if data.get("schema") != {"version": "preview"}:
        _fail("audit report: unsupported uv audit schema")
    summary = _table(data.get("summary"), "audit report summary")
    for key in ("audited_packages", "vulnerabilities", "adverse_statuses"):
        if type(summary.get(key)) is not int or summary[key] < 0:
            _fail(f"audit report summary.{key}: expected a nonnegative integer")
    if not summary["audited_packages"]:
        _fail("audit report: zero packages audited cannot claim a passing gate")
    for key in ("vulnerabilities", "adverse_statuses"):
        findings = _rows(data.get(key), f"audit report {key}")
        if len(findings) != summary[key]:
            _fail(f"audit report {key}: summary count differs from findings")
        if findings:
            _fail(f"audit report: {len(findings)} {key} require remediation")
    return {
        key: summary[key]
        for key in ("audited_packages", "vulnerabilities", "adverse_statuses")
    }


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    """Check lock trust or export an SBOM, failing closed on invalid inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=repo_root or REPO_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "check", help="validate dependency trust inputs offline"
    )
    check.add_argument(
        "--json", action="store_true", help="emit machine-readable counts"
    )
    sbom = commands.add_parser("sbom", help="export deterministic CycloneDX JSON")
    sbom.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    audit = commands.add_parser(
        "audit-report", help="validate clean uv audit JSON evidence"
    )
    audit.add_argument("path", type=Path, help="JSON report produced by uv audit")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            summary = check_policy(args.repo_root)
            print(
                json.dumps(summary, sort_keys=True)
                if args.json
                else f"supply chain policy valid: {summary['python_packages']} Python packages, {summary['python_artifacts']} hashed artifacts, {summary['rust_packages']} Rust packages"
            )
        elif args.command == "audit-report":
            summary = validate_audit(args.path)
            print(
                f"supply chain audit valid: {summary['audited_packages']} packages, no vulnerabilities or adverse statuses"
            )
        else:
            encoded = (
                json.dumps(build_sbom(args.repo_root), indent=2, sort_keys=True) + "\n"
            )
            if args.output is None:
                print(encoded, end="")
            else:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded, encoding="utf-8")
    except (SupplyChainError, OSError) as exc:
        print(f"supply chain: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
