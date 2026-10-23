"""Audio admission trusts exact reviewed binaries, not version or search-path claims."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.support.environment.backends import require_backend
from tests.tooling.runtime import audio


def _manifest():
    return {
        "schema": 1,
        "recipe": audio.RECIPE,
        "inputs": {
            "source_sha256": audio.SOURCE_SHA256,
            "official_patch_sha256": audio.PATCH_SHA256,
            "backport_sha256": audio.BACKPORT_SHA256,
            "common_before_sha256": audio.COMMON_BEFORE_SHA256,
            "common_after_sha256": audio.COMMON_AFTER_SHA256,
            "fix_commit": audio.FIX_COMMIT,
        },
        "output": {
            "library_sha256": "a" * 64,
            "native_version": "libsndfile-1.2.2",
        },
    }


def _admit(manifest, loaded=None):
    data = json.dumps(manifest).encode()
    return audio.provenance_errors(
        loaded or _manifest()["output"], data, [hashlib.sha256(data).hexdigest()]
    )


def test_exact_reviewed_source_fix_and_loaded_binary_can_be_admitted():
    assert _admit(_manifest()) == ()


def test_identical_version_does_not_admit_another_binary():
    loaded = {"library_sha256": "b" * 64, "native_version": "libsndfile-1.2.2"}
    assert "actual loaded libsndfile hash" in _admit(_manifest(), loaded)[0]


def test_a_self_asserted_manifest_needs_a_separate_trust_anchor():
    data = json.dumps(_manifest()).encode()
    assert audio.provenance_errors(_manifest()["output"], data, ()) == (
        "provenance manifest is not in the separately trusted review policy",
    )


def test_missing_output_hash_cannot_match_missing_loaded_evidence():
    manifest = _manifest()
    manifest["output"] = {}
    data = json.dumps(manifest).encode()
    assert audio.provenance_errors({}, data, [hashlib.sha256(data).hexdigest()])


@pytest.mark.parametrize("field", list(_manifest()["inputs"]))
def test_each_authenticated_source_and_fix_field_is_required(field):
    manifest = _manifest()
    manifest["inputs"][field] = "unreviewed"
    assert field in _admit(manifest)[0]


@pytest.mark.parametrize("schema", [True, False, "1", 1.0, 2, None])
def test_schema_admission_is_exact_and_never_accepts_bool_as_one(schema):
    manifest = _manifest()
    manifest["schema"] = schema
    assert _admit(manifest)


@pytest.mark.parametrize("manifest", [[], None, {"schema": 1}, {"inputs": []}])
def test_missing_or_malformed_provenance_fails_closed(manifest):
    assert _admit(manifest)


def test_even_trusted_but_malformed_json_is_a_rejection():
    data = b"not JSON"
    errors = audio.provenance_errors({}, data, [hashlib.sha256(data).hexdigest()])
    assert "cannot parse reviewed provenance" in errors[0]


@pytest.mark.parametrize("schema", [True, "1", 1.0])
def test_trust_policy_schema_is_exact(tmp_path, schema):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema": schema, "provenance_sha256": []}))
    with pytest.raises(ValueError, match="trust-policy schema"):
        audio.trusted_manifest_digests(path)


@pytest.mark.parametrize("digests", [None, "a" * 64, ["a"], [True], ["A" * 64]])
def test_trust_policy_requires_complete_sha256_digests(tmp_path, digests):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema": 1, "provenance_sha256": digests}))
    with pytest.raises(ValueError, match="SHA256"):
        audio.trusted_manifest_digests(path)


def test_loader_resolution_ignores_a_forged_soundfile_search_path(monkeypatch):
    soundfile = require_backend("soundfile")
    before = audio.loaded_library_report()
    monkeypatch.setattr(soundfile, "_full_path", "/unreviewed/not-the-loaded-library")
    after = audio.loaded_library_report()
    assert after["symbol_address"] == before["symbol_address"]
    assert after["library_path"] == before["library_path"]
    assert after["library_sha256"] == before["library_sha256"]


def test_a_null_native_address_is_never_resolved():
    with pytest.raises(ValueError, match="no address"):
        audio.library_path_for_address(0)


@pytest.mark.parametrize("policy_body", [None, "not JSON", "[]"])
def test_runtime_missing_or_bad_policy_reports_rejection(
    monkeypatch, tmp_path, policy_body
):
    monkeypatch.setattr(audio, "loaded_library_report", lambda: _manifest()["output"])
    manifest = tmp_path / "provenance.json"
    manifest.write_text(json.dumps(_manifest()))
    policy = tmp_path / "policy.json"
    if policy_body is not None:
        policy.write_text(policy_body)
    report = audio.runtime_report(manifest, policy)
    assert report["errors"]


def test_cli_structured_admission_uses_loaded_binary_and_reviewed_manifest(
    monkeypatch, tmp_path, capsys
):
    manifest = tmp_path / "provenance.json"
    manifest.write_text(json.dumps(_manifest()))
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"schema": 1, "provenance_sha256": [audio.file_sha256(manifest)]})
    )
    loaded = _manifest()["output"]
    monkeypatch.setattr(audio, "loaded_library_report", lambda: loaded)
    args = ["--json", "--provenance", str(manifest), "--trust-policy", str(policy)]
    assert audio.main(args) == 0
    assert json.loads(capsys.readouterr().out)["errors"] == []
    loaded["library_sha256"] = "b" * 64
    assert audio.main(args) == 1
    assert json.loads(capsys.readouterr().out)["errors"]
