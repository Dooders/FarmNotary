import json
from pathlib import Path

import pytest

from farm_notary.manifest import (
    Manifest,
    build_manifest,
    load_manifest,
    write_manifest,
)
from farm_notary.verify import verify_run_dir


def make_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "round_1.json").write_text('{"score": 1}', encoding="utf-8")
    return tmp_path


def test_build_and_verify(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, git_sha="abc", config={"trials": 2})
    assert "summary.csv" in manifest.artifact_hashes
    write_manifest(manifest, tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []


def test_recursive_discovery_uses_posix_relative_paths(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path)
    assert manifest.artifacts == ["metrics/round_1.json", "summary.csv"]
    assert set(manifest.artifacts) == set(manifest.artifact_hashes)


def test_private_hidden_and_manifest_files_are_skipped(tmp_path: Path):
    make_run_dir(tmp_path)
    (tmp_path / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "choices.csv").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".hidden.log").write_text("noise\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    manifest = build_manifest(tmp_path)
    assert manifest.artifacts == ["metrics/round_1.json", "summary.csv"]


def test_manifest_round_trip(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, git_sha="abc", runner="consensus", config={"n": 3})
    write_manifest(manifest, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded == manifest
    # Loading via the explicit file path works too.
    assert load_manifest(tmp_path / "manifest.json") == manifest


def test_content_hash_excludes_cid_and_chain(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path)
    before = manifest.content_hash()
    manifest.cid = "bafyexample"
    manifest.chain = {"backend": "dry-run"}
    assert manifest.content_hash() == before


def test_from_dict_ignores_unknown_keys(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path)
    data = dict(manifest.to_dict(), future_field="whatever")
    assert Manifest.from_dict(data) == manifest


def test_validate_rejects_artifact_hash_mismatch(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path)
    manifest.artifacts.append("ghost.csv")
    with pytest.raises(ValueError, match="disagree"):
        manifest.validate()


def test_validate_rejects_private_artifacts():
    manifest = Manifest(
        created_utc="2026-01-01T00:00:00Z",
        artifacts=["votes_ballot.csv"],
        artifact_hashes={"votes_ballot.csv": "0" * 64},
    )
    with pytest.raises(ValueError, match="private"):
        manifest.validate()


def test_validate_rejects_unknown_schema(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path)
    data = manifest.to_dict()
    data["schema"] = "farmnotary.manifest.v999"
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_manifest(tmp_path)
