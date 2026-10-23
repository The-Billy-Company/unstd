"""Pinned audio inputs and same-job build receipts cannot silently become bypasses."""

from __future__ import annotations

import json

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.runtime import audio, audio_build


def test_vendored_official_patch_and_exact_backport_match_the_reviewed_pins():
    patch = REPO_ROOT / "native/audio/upstream-474e4d3.patch"
    official = audio_build.authenticated_bytes(patch, audio.PATCH_SHA256)
    assert official.startswith(f"From {audio.FIX_COMMIT} ".encode())
    assert official.count(b"read_bytes = header_read") == 13
    adapted = audio_build.backport_bytes(official)
    assert adapted.count(b"byte_count += header_read") == 13
    assert b"store_uint (intptr, psf_get_le32 (sixteen_bytes, 0))" in adapted
    assert b"store_float (floatptr, float32_le_read (sixteen_bytes))" in adapted
    assert b"store_double (doubleptr, double64_le_read (sixteen_bytes))" in adapted


def test_modified_patch_is_refused_before_backporting():
    patch = REPO_ROOT / "native/audio/upstream-474e4d3.patch"
    with pytest.raises(ValueError, match="not been authenticated"):
        audio_build.backport_bytes(patch.read_bytes() + b"\n")


def test_unverified_source_is_rejected_before_creating_output(tmp_path):
    source = tmp_path / "source.tar.xz"
    source.write_bytes(b"unreviewed archive")
    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        audio_build.build(source, source, output, source.resolve())
    assert not output.exists()


def test_wrong_authenticated_input_hash_is_not_an_allowed_override(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"contents")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        audio_build.authenticated_bytes(source, "0" * 64)


def test_static_codec_snapshot_survives_package_manager_source_replacement(tmp_path):
    source = tmp_path / "system-codec.a"
    source.write_bytes(b"reviewed codec archive bytes")
    staged = tmp_path / "private-build-codec.a"
    evidence = audio_build._stage_codec_archive(source, staged)
    expected_hash = audio.file_sha256(staged)
    source.write_bytes(b"updated archive from a package manager")
    assert staged.read_bytes() == b"reviewed codec archive bytes"
    assert evidence["path"] == str(staged)
    assert evidence["sha256"] == expected_hash
    assert audio.file_sha256(source) != expected_hash


def test_failed_build_cannot_emit_a_trust_policy(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        message = "source or native capability rejected"
        raise ValueError(message)

    monkeypatch.setattr(audio_build, "build", fail)
    policy = tmp_path / "trusted.json"
    assert (
        audio_build.main(
            [
                "--source",
                "source",
                "--patch",
                "patch",
                "--output",
                "output",
                "--cmake",
                "/cmake",
                "--trust-policy",
                str(policy),
            ]
        )
        == 1
    )
    assert not policy.exists()
    assert "rejected" in capsys.readouterr().err


def test_policy_is_created_only_from_the_successful_same_invocation_build(
    monkeypatch, tmp_path, capsys
):
    manifest = tmp_path / "provenance.json"
    manifest.write_text('{"trusted build receipt": true}\n')
    built = []

    def finish(*args, **kwargs):
        built.append(True)
        return manifest

    monkeypatch.setattr(audio_build, "build", finish)
    policy = tmp_path / "trusted.json"
    args = [
        "--source",
        "source",
        "--patch",
        "patch",
        "--output",
        "output",
        "--cmake",
        "/cmake",
        "--trust-policy",
        str(policy),
    ]
    assert audio_build.main(args) == 0
    assert built == [True]
    assert audio.trusted_manifest_digests(policy) == (audio.file_sha256(manifest),)
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["provenance_sha256"] == audio.file_sha256(manifest)
    before = policy.read_bytes()
    with pytest.raises(SystemExit):
        audio_build.main(args)
    assert built == [True]
    assert policy.read_bytes() == before
