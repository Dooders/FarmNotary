"""Tests for CI provenance detection and verification.

``detect_ci_provenance`` reads GitHub Actions env vars and records them in
the manifest.  ``verify_ci_provenance`` checks that ``git_sha`` agrees with
the CI-attested SHA.  Disagreement is a hard problem (verify fails).
"""

from pathlib import Path

import pytest

from farm_notary.manifest import build_manifest, detect_ci_provenance, load_manifest, write_manifest
from farm_notary.verify import evaluate_claims, verify_ci_provenance


# ---------------------------------------------------------------------------
# detect_ci_provenance
# ---------------------------------------------------------------------------

def test_detect_ci_provenance_absent_outside_ci():
    """No GITHUB_ACTIONS → no provenance."""
    # conftest autouse fixture already clears GITHUB_ACTIONS
    assert detect_ci_provenance() is None


def test_detect_ci_provenance_returns_none_when_flag_not_true(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    assert detect_ci_provenance() is None


def test_detect_ci_provenance_returns_none_without_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # GITHUB_SHA not set
    assert detect_ci_provenance() is None


def test_detect_ci_provenance_minimal(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef" * 5)
    prov = detect_ci_provenance()
    assert prov is not None
    assert prov["kind"] == "github_actions"
    assert prov["sha"] == "deadbeef" * 5
    # Optional fields absent when env vars not set
    assert "repository" not in prov
    assert "run_id" not in prov
    # run_url absent when RUN_ID / REPOSITORY not set
    assert "run_url" not in prov


def test_detect_ci_provenance_full(monkeypatch):
    sha = "a" * 40
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Dooders/FarmNotary")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_WORKFLOW", "CI")
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    prov = detect_ci_provenance()
    assert prov["sha"] == sha
    assert prov["repository"] == "Dooders/FarmNotary"
    assert prov["ref"] == "refs/heads/main"
    assert prov["workflow"] == "CI"
    assert prov["run_id"] == "123456"
    assert prov["run_url"] == "https://github.com/Dooders/FarmNotary/actions/runs/123456"


# ---------------------------------------------------------------------------
# build_manifest ci_provenance integration
# ---------------------------------------------------------------------------

def _run_dir(tmp_path: Path) -> Path:
    (tmp_path / "out.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return tmp_path


def test_build_manifest_no_ci_provenance_outside_ci(tmp_path):
    manifest = build_manifest(_run_dir(tmp_path), publish_patterns=["*.csv"], git_sha="abc", git_dirty=False)
    assert manifest.ci_provenance is None


def test_build_manifest_records_ci_provenance_in_ci(monkeypatch, tmp_path):
    sha = "b" * 40
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Dooders/FarmNotary")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    manifest = build_manifest(
        _run_dir(tmp_path), publish_patterns=["*.csv"], git_sha=sha, git_dirty=False
    )
    assert manifest.ci_provenance is not None
    assert manifest.ci_provenance["sha"] == sha
    assert manifest.ci_provenance["kind"] == "github_actions"


def test_build_manifest_explicit_none_suppresses_ci_provenance(monkeypatch, tmp_path):
    """Passing ci_provenance=None opts out of auto-detection even in CI."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    manifest = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        ci_provenance=None,
    )
    assert manifest.ci_provenance is None


def test_build_manifest_explicit_provenance_overrides_detection(monkeypatch, tmp_path):
    """A caller-supplied ci_provenance dict is recorded as-is."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "d" * 40)
    custom = {"kind": "github_actions", "sha": "abc", "repository": "org/repo"}
    manifest = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        ci_provenance=custom,
    )
    assert manifest.ci_provenance == custom


# ---------------------------------------------------------------------------
# ci_provenance is included in content_hash
# ---------------------------------------------------------------------------

def test_ci_provenance_changes_content_hash(tmp_path):
    """ci_provenance is part of content_hash so the anchor commits to it."""
    base = build_manifest(
        _run_dir(tmp_path), publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )
    with_prov = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        ci_provenance={"kind": "github_actions", "sha": "abc", "repository": "org/repo"},
    )
    assert base.content_hash() != with_prov.content_hash()


# ---------------------------------------------------------------------------
# ci_provenance omitted from serialization when None
# ---------------------------------------------------------------------------

def test_ci_provenance_omitted_from_dict_when_none(tmp_path):
    manifest = build_manifest(
        _run_dir(tmp_path), publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )
    d = manifest.to_dict()
    assert "ci_provenance" not in d


def test_ci_provenance_round_trips_through_json(tmp_path):
    prov = {"kind": "github_actions", "sha": "abc", "repository": "org/repo"}
    manifest = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        ci_provenance=prov,
    )
    write_manifest(manifest, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded.ci_provenance == prov


# ---------------------------------------------------------------------------
# verify_ci_provenance
# ---------------------------------------------------------------------------

def _manifest_with_prov(git_sha, prov_sha):
    from types import SimpleNamespace
    return SimpleNamespace(
        git_sha=git_sha,
        ci_provenance={"kind": "github_actions", "sha": prov_sha} if prov_sha is not None else None,
    )


def test_verify_ci_provenance_no_provenance():
    """No ci_provenance → no problems (local run)."""
    m = _manifest_with_prov("abc", None)
    assert verify_ci_provenance(m) == []


def test_verify_ci_provenance_matching_sha():
    """Matching shas → no problems."""
    sha = "a" * 40
    m = _manifest_with_prov(sha, sha)
    assert verify_ci_provenance(m) == []


def test_verify_ci_provenance_disagreement():
    """Disagreeing shas → one problem reported."""
    m = _manifest_with_prov("abc123", "def456")
    problems = verify_ci_provenance(m)
    assert len(problems) == 1
    assert "abc123" in problems[0]
    assert "def456" in problems[0]
    assert "disagrees" in problems[0]


def test_verify_ci_provenance_empty_manifest_git_sha():
    """No git_sha on manifest with ci_provenance → problem (cannot verify binding)."""
    m = _manifest_with_prov(None, "a" * 40)
    problems = verify_ci_provenance(m)
    assert len(problems) == 1
    assert "missing" in problems[0]


# ---------------------------------------------------------------------------
# evaluate_claims integration
# ---------------------------------------------------------------------------

def test_evaluate_claims_ci_provenance_note_when_matching(tmp_path):
    """A matching ci_provenance produces a CI-attested note, not a problem."""
    sha = "e" * 40
    manifest = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha=sha,
        git_dirty=False,
        ci_provenance={"kind": "github_actions", "sha": sha, "repository": "org/repo"},
    )
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert any("Recorded CI SHA" in n for n in card.notes)
    assert any("org/repo" in n for n in card.notes)


def test_evaluate_claims_ci_provenance_fails_on_disagreement(tmp_path):
    """A mismatched ci_provenance SHA makes card.ok False (verify fails)."""
    manifest = build_manifest(
        _run_dir(tmp_path),
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        ci_provenance={"kind": "github_actions", "sha": "def456"},
    )
    card = evaluate_claims(manifest, tmp_path)
    assert not card.ok
    assert any("disagrees" in p for p in card.problems)


def test_evaluate_claims_no_ci_provenance_still_ok(tmp_path):
    """Local runs (no ci_provenance) remain valid at lower claim level."""
    manifest = build_manifest(
        _run_dir(tmp_path), publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )
    assert manifest.ci_provenance is None
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert not any("CI-attested" in n for n in card.notes)
