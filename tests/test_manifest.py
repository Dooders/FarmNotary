import json
import warnings
from pathlib import Path

import pytest

from farm_notary import __version__
from farm_notary.manifest import (
    Manifest,
    build_manifest,
    hash_json,
    is_private_path,
    load_manifest,
    write_manifest,
)
from farm_notary.schema import MANIFEST_VERSION
from farm_notary.verify import verify_run_dir

PUBLISH_ALL = ["*", "**/*"]


def make_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "round_1.json").write_text('{"score": 1}', encoding="utf-8")
    return tmp_path


def test_build_and_verify(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv", "**/*.json"], git_sha="abc", config={"trials": 2})
    assert "summary.csv" in manifest.artifact_hashes
    write_manifest(manifest, tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []


def test_recursive_discovery_uses_posix_relative_paths(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv", "**/*.json"])
    assert manifest.artifacts == ["metrics/round_1.json", "summary.csv"]
    assert set(manifest.artifacts) == set(manifest.artifact_hashes)


def test_allowlist_gates_files(tmp_path: Path):
    """Only files matching a publish pattern are included."""
    make_run_dir(tmp_path)
    # Only publish CSVs; the JSON should be excluded.
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    assert manifest.artifacts == ["summary.csv"]
    assert "metrics/round_1.json" not in manifest.artifact_hashes
    assert manifest.unmatched_count == 1


def test_agent_selections_excluded_by_default(tmp_path: Path):
    """agent_selections.csv (no denylist match) is excluded when not in allowlist."""
    make_run_dir(tmp_path)
    (tmp_path / "agent_selections.csv").write_text("agent,choice\n1,A\n", encoding="utf-8")
    # Only publish summary.csv explicitly — agent_selections.csv is NOT declared.
    manifest = build_manifest(tmp_path, publish_patterns=["summary.csv"])
    assert "agent_selections.csv" not in manifest.artifacts
    assert manifest.unmatched_count >= 2  # agent_selections.csv and round_1.json excluded


def test_no_publish_patterns_raises(tmp_path: Path):
    """build_manifest raises when no patterns are declared."""
    make_run_dir(tmp_path)
    with pytest.raises(ValueError, match="publish patterns"):
        build_manifest(tmp_path)


def test_publish_patterns_from_config(tmp_path: Path):
    """Patterns declared in config['notary']['publish'] are respected."""
    make_run_dir(tmp_path)
    config = {"notary": {"publish": ["*.csv"]}, "trials": 1}
    manifest = build_manifest(tmp_path, config=config)
    assert manifest.artifacts == ["summary.csv"]
    assert manifest.publish_patterns == ["*.csv"]


def test_publish_patterns_cli_plus_config(tmp_path: Path):
    """CLI patterns are appended after config patterns."""
    make_run_dir(tmp_path)
    config = {"notary": {"publish": ["*.csv"]}}
    manifest = build_manifest(tmp_path, publish_patterns=["**/*.json"], config=config)
    assert set(manifest.artifacts) == {"summary.csv", "metrics/round_1.json"}
    assert "*.csv" in manifest.publish_patterns
    assert "**/*.json" in manifest.publish_patterns


def test_unmatched_count_and_warning(tmp_path: Path):
    make_run_dir(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    assert manifest.unmatched_count == 1  # round_1.json not matched
    assert any("excluded" in str(w.message).lower() for w in caught)


def test_no_warning_when_all_matched(tmp_path: Path):
    make_run_dir(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_manifest(tmp_path, publish_patterns=PUBLISH_ALL)
    assert not any(issubclass(w.category, UserWarning) for w in caught)


def test_denylist_still_applied_after_allowlist(tmp_path: Path):
    """A publish pattern that would match a private-named file is still blocked."""
    make_run_dir(tmp_path)
    (tmp_path / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    assert "votes_ballot.csv" not in manifest.artifacts


def test_private_hidden_and_manifest_files_are_skipped(tmp_path: Path):
    make_run_dir(tmp_path)
    (tmp_path / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "choices.csv").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".hidden.log").write_text("noise\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=PUBLISH_ALL)
    assert manifest.artifacts == ["metrics/round_1.json", "summary.csv"]


def test_manifest_round_trip(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv", "**/*.json"], git_sha="abc", runner="consensus", config={"n": 3})
    write_manifest(manifest, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded == manifest
    # Loading via the explicit file path works too.
    assert load_manifest(tmp_path / "manifest.json") == manifest


def test_content_hash_excludes_cid_and_anchor(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    before = manifest.content_hash()
    manifest.cid = "bafyexample"
    manifest.anchor = {"backend": "dry-run"}
    assert manifest.content_hash() == before


def test_from_dict_ignores_unknown_keys(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    data = dict(manifest.to_dict(), future_field="whatever")
    assert Manifest.from_dict(data) == manifest


def test_validate_rejects_artifact_hash_mismatch(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    manifest.artifacts.append("ghost.csv")
    with pytest.raises(ValueError, match="disagree"):
        manifest.validate()


def test_validate_rejects_private_artifacts():
    manifest = Manifest(
        created_utc="2026-01-01T00:00:00Z",
        artifacts=["votes_ballot.csv"],
        artifact_hashes={"votes_ballot.csv": "0" * 64},
        publish_patterns=["*.csv"],
        unmatched_count=0,
    )
    with pytest.raises(ValueError, match="private"):
        manifest.validate()


def test_environment_captured_by_default(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    assert manifest.environment["python"]
    assert manifest.environment["system"]
    assert manifest.environment["machine"]
    assert len(manifest.environment["packages_hash"]) == 64
    assert manifest.environment["package_count"] > 0


def test_lockfile_hash_recorded(tmp_path: Path):
    make_run_dir(tmp_path)
    lock = tmp_path / "requirements.lock"
    lock.write_text("numpy==2.0.0\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc", lockfile=lock)
    assert manifest.environment["lockfile"] == "requirements.lock"
    assert len(manifest.environment["lockfile_sha256"]) == 64


def test_command_and_environment_are_anchored(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc", command="run {run_dir}")
    before = manifest.content_hash()
    manifest.command = "something-else {run_dir}"
    assert manifest.content_hash() != before


def test_git_status_detection(tmp_path: Path):
    import subprocess

    from farm_notary.manifest import detect_git_status

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=repo, check=True)

    sha, dirty = detect_git_status(cwd=repo)
    assert sha and len(sha) == 40
    assert dirty is False

    (repo / "f.txt").write_text("changed", encoding="utf-8")
    sha2, dirty2 = detect_git_status(cwd=repo)
    assert sha2 == sha
    assert dirty2 is True

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert detect_git_status(cwd=outside) == (None, None)


def test_validate_rejects_unknown_schema(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"])
    data = manifest.to_dict()
    data["schema"] = "farmnotary.manifest.v999"
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_manifest(tmp_path)


def test_is_private_path():
    assert is_private_path("votes_ballot.csv") is True
    assert is_private_path("private/choices.csv") is True
    assert is_private_path("voter_registry.json") is True
    assert is_private_path("individual_choice.csv") is True
    assert is_private_path("summary.csv") is False
    assert is_private_path("metrics/round_1.json") is False
    assert is_private_path("BALLOT_SUMMARY.CSV") is True  # case-insensitive


def test_farm_notary_version_in_manifest(tmp_path: Path):
    """build_manifest records the tool version in every manifest."""
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    assert manifest.farm_notary_version == __version__


def test_farm_notary_version_survives_round_trip(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded.farm_notary_version == __version__


def test_farm_notary_version_in_serialised_dict(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    d = manifest.to_dict()
    assert "farm_notary_version" in d
    assert d["farm_notary_version"] == __version__


def test_load_v1_manifest_without_farm_notary_version_preserves_hash(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    data = manifest.to_dict()
    data.pop("farm_notary_version", None)
    expected_hash = hash_json(data)
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    loaded = load_manifest(tmp_path)
    assert loaded.farm_notary_version is None
    assert loaded.content_hash() == expected_hash


def test_schema_version_skew_warning(tmp_path: Path):
    """verify_run_dir warns when the manifest schema is newer than the running tool's."""
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    # Simulate a manifest produced by a hypothetical future tool with a newer schema.
    manifest.schema = "farmnotary.manifest.v9999"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        verify_run_dir(manifest, tmp_path)
    assert any(
        "newer" in str(w.message).lower() and MANIFEST_VERSION in str(w.message)
        for w in caught
    )


def test_no_schema_skew_warning_for_current_schema(tmp_path: Path):
    """verify_run_dir does not warn when the manifest schema matches the tool's schema."""
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        verify_run_dir(manifest, tmp_path)
    assert not any("newer" in str(w.message).lower() for w in caught)
