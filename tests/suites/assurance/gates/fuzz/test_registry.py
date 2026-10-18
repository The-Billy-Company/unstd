"""Invalid registry or corpus evidence fails closed before target execution."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from tests.tooling.fuzz import registry


_ROW = {
    "name": "sample",
    "backends": ["installed", "stdlib"],
    "requirements": ["package.backend"],
    "input_version": 1,
    "max_input_bytes": 65536,
    "minimum_seeds": 12,
    "surface": "Reviewed parser API",
    "oracle": "Independent reference",
}


def _inventory(root, rows=None, **header):
    rows = [_ROW] if rows is None else rows
    lines = [
        f"{key} = {json.dumps(value)}"
        for key, value in {"version": 1, **header}.items()
    ]
    for row in rows:
        lines.append("[[target]]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in row.items())
    (root / "registry.toml").write_text("\n".join(lines))


def _manifest(root):
    return json.loads((root / "corpus/sample/seeds.json").read_text())


def _save(root, doc):
    (root / "corpus/sample/seeds.json").write_text(json.dumps(doc))


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets/sample.py").write_text(
        "def fuzz(data: bytes) -> None:\n    pass\n"
    )
    _inventory(tmp_path)
    directory = tmp_path / "corpus/sample"
    directory.mkdir(parents=True)
    entries = []
    for index in range(12):
        data = bytes(range(index))
        filename = f"case-{index:02}.bin"
        (directory / filename).write_bytes(data)
        entries.append(
            {
                "file": filename,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "label": f"Reviewed case {index}",
                "kind": ("valid", "invalid", "boundary")[index % 3],
                "origin": "reviewed synthetic gate fixture",
            }
        )
    _save(
        tmp_path,
        {"version": 1, "target": "sample", "input_version": 1, "seeds": entries},
    )
    return tmp_path


def test_committed_registry_and_all_reviewed_corpora_validate():
    targets = registry.check()
    assert len(targets) == 11
    assert {target.name for target in targets} == {
        "json",
        "base64",
        "ndjson",
        "structs",
        "tokens",
        "pack",
        "wav",
        "toml",
        "yaml",
        "rex",
        "gzip",
    }
    for target in targets:
        seeds = registry.load_seeds(target)
        assert len(seeds) >= target.minimum_seeds >= 12
        assert seeds == tuple(sorted(seeds, key=lambda seed: seed.file))
        assert all(seed.identity == f"{target.name}/{seed.file}" for seed in seeds)


def test_fixture_is_complete_and_zero_length_seed_is_real_evidence(tree):
    (target,) = registry.check(tree)
    seeds = registry.load_seeds(target, tree)
    assert target.module == "tests.tooling.fuzz.targets.sample"
    assert seeds[0].data == b""
    assert len(seeds) == 12
    assert registry.corpus_digest(seeds) == registry.corpus_digest(
        tuple(reversed(seeds))
    )
    changed = (replace(seeds[0], data=b"changed"), *seeds[1:])
    assert registry.corpus_digest(changed) != registry.corpus_digest(seeds)
    renamed = (replace(seeds[0], file="renamed.bin"), *seeds[1:])
    assert registry.corpus_digest(renamed) != registry.corpus_digest(seeds)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "../escape"),
        ("name", "not-a-module"),
        ("name", ""),
        ("backends", []),
        ("backends", ["installed", "installed"]),
        ("backends", ["unknown"]),
        ("backends", [[]]),
        ("requirements", []),
        ("requirements", ["bad-name"]),
        ("requirements", ["pkg..backend"]),
        ("requirements", [[1]]),
        ("requirements", ["pkg", "pkg"]),
        ("max_input_bytes", 0),
        ("max_input_bytes", 65537),
        ("max_input_bytes", True),
        ("minimum_seeds", 11),
        ("minimum_seeds", True),
        ("input_version", 2),
        ("input_version", True),
        ("surface", "  "),
        ("oracle", 0),
    ],
)
def test_registry_rejects_bad_field_types_values_duplicates_and_floors(
    tree, field, value
):
    _inventory(tree, [{**_ROW, field: value}])
    with pytest.raises(registry.RegistryError):
        registry.load_registry(tree)


@pytest.mark.parametrize(
    "change", ["extra", "missing", "duplicate", "empty", "header", "bad-version"]
)
def test_registry_schema_and_identity_inventory_are_exact(tree, change):
    if change == "extra":
        _inventory(tree, [{**_ROW, "unreviewed": 1}])
    elif change == "missing":
        _inventory(
            tree, [{key: value for key, value in _ROW.items() if key != "oracle"}]
        )
    elif change == "duplicate":
        _inventory(tree, [_ROW, _ROW])
    elif change == "empty":
        _inventory(tree, [])
    elif change == "header":
        _inventory(tree, surprise=1)
    else:
        _inventory(tree, version=True)
    with pytest.raises(registry.RegistryError):
        registry.load_registry(tree)


@pytest.mark.parametrize(
    "source",
    [
        "def different(data): pass",
        "async def fuzz(data): pass",
        "def fuzz(): pass",
        "def fuzz(data, extra): pass",
        "def fuzz(data=None): pass",
        "def fuzz(data, *args): pass",
        "def fuzz(data, **kw): pass",
        "def fuzz(data, *, optional=None): pass",
        "def fuzz(data): pass\ndef fuzz(other): pass",
        "invalid Python :",
    ],
)
def test_target_source_requires_one_synchronous_exact_entrypoint(tree, source):
    (tree / "targets/sample.py").write_text(source)
    with pytest.raises(registry.RegistryError):
        registry.load_registry(tree)


def test_discovery_rejects_uninventoried_target_and_missing_target(tree):
    extra = tree / "targets/unreviewed.py"
    extra.write_text("def fuzz(data): pass")
    with pytest.raises(registry.RegistryError, match="discovery drift"):
        registry.load_registry(tree)
    extra.unlink()
    (tree / "targets/sample.py").unlink()
    with pytest.raises(registry.RegistryError, match="missing"):
        registry.load_registry(tree)


@pytest.mark.parametrize(
    "where",
    [
        "registry.toml",
        "targets/sample.py",
        "targets",
        "corpus/sample/seeds.json",
        "corpus/sample/case-00.bin",
        "corpus/sample",
        "corpus",
    ],
)
def test_symlinked_metadata_sources_directories_and_inputs_are_refused(tree, where):
    path = tree / where
    moved = path.with_name(path.name + "-real")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(registry.RegistryError, match="symlink"):
        registry.check(tree)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", 2),
        ("target", "other"),
        ("input_version", True),
        ("input_version", 2),
        ("seeds", []),
        ("seeds", {}),
    ],
)
def test_corpus_manifest_version_identity_type_and_floor_are_strict(tree, field, value):
    doc = _manifest(tree)
    doc[field] = value
    _save(tree, doc)
    with pytest.raises(registry.RegistryError):
        registry.check(tree)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("file", "../escape.bin"),
        ("file", "/absolute.bin"),
        ("file", "UPPER.bin"),
        ("file", "a.txt"),
        ("sha256", "0" * 64),
        ("sha256", "A" * 64),
        ("sha256", []),
        ("size", True),
        ("size", -1),
        ("size", 65537),
        ("size", 1),
        ("kind", []),
        ("kind", "unreviewed"),
        ("label", "  "),
        ("origin", None),
    ],
)
def test_seed_schema_hash_size_paths_kinds_and_provenance_are_strict(
    tree, field, value
):
    doc = _manifest(tree)
    doc["seeds"][0][field] = value
    _save(tree, doc)
    with pytest.raises(registry.RegistryError):
        registry.check(tree)


@pytest.mark.parametrize(
    "change",
    [
        "extra-manifest",
        "missing-manifest",
        "extra-seed",
        "missing-seed",
        "duplicate-name",
        "duplicate-bytes",
        "missing-kind",
        "orphan-file",
        "orphan-directory",
        "extra-corpus",
        "root-orphan",
        "truncated",
        "oversized",
        "below-floor",
    ],
)
def test_corpus_drift_never_becomes_successful_evidence(tree, change):
    doc = _manifest(tree)
    directory = tree / "corpus/sample"
    if change == "extra-manifest":
        doc["extra"] = 1
    elif change == "missing-manifest":
        del doc["version"]
    elif change == "extra-seed":
        doc["seeds"][0]["extra"] = 1
    elif change == "missing-seed":
        (directory / doc["seeds"][0]["file"]).unlink()
    elif change == "duplicate-name":
        doc["seeds"][1]["file"] = doc["seeds"][0]["file"]
    elif change == "duplicate-bytes":
        (directory / doc["seeds"][1]["file"]).write_bytes(b"")
        doc["seeds"][1].update(sha256=hashlib.sha256(b"").hexdigest(), size=0)
    elif change == "missing-kind":
        for row in doc["seeds"]:
            row["kind"] = "valid"
    elif change == "orphan-file":
        (directory / "orphan.bin").touch()
    elif change == "orphan-directory":
        (directory / "orphan").mkdir()
    elif change == "extra-corpus":
        (tree / "corpus/extra").mkdir()
    elif change == "root-orphan":
        (tree / "corpus/unreviewed.txt").touch()
    elif change == "truncated":
        (directory / doc["seeds"][5]["file"]).write_bytes(b"x")
    elif change == "oversized":
        (directory / doc["seeds"][0]["file"]).write_bytes(b"x" * 65537)
    else:
        doc["seeds"].pop()
    _save(tree, doc)
    with pytest.raises(registry.RegistryError):
        registry.check(tree)


@pytest.mark.parametrize(
    "raw", [b"[]", b"null", b"{", b"\xff", b'{"version":1,"version":1}']
)
def test_bad_json_and_duplicate_manifest_keys_are_diagnosed(tree, raw):
    (tree / "corpus/sample/seeds.json").write_bytes(raw)
    with pytest.raises(registry.RegistryError):
        registry.check(tree)


def test_seed_loading_never_uses_unbounded_read_bytes(tree, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("seed read must be bounded")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert len(registry.load_seeds(registry.load_registry(tree)[0], tree)) == 12
