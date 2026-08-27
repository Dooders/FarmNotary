from pathlib import Path

from farm_notary.anchor import anchor_run
from farm_notary.manifest import build_manifest
from farm_notary.ots import PROOF_NAME, serialize_proof
from farm_notary.verify import verify_anchor, verify_run_dir
from tests.test_ots import pending_timestamp


def make_manifest(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return build_manifest(tmp_path, git_sha="abc")


def write_proof_for(manifest, run_dir: Path, digest_hex=None) -> Path:
    digest = bytes.fromhex(digest_hex or manifest.content_hash())
    path = run_dir / PROOF_NAME
    path.write_bytes(serialize_proof(pending_timestamp(digest, "https://example.com")))
    return path


def test_verify_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []


def test_verify_detects_tampering(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.9\n", encoding="utf-8")
    assert verify_run_dir(manifest, tmp_path) == ["hash mismatch: summary.csv"]


def test_verify_detects_missing_artifact(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").unlink()
    assert verify_run_dir(manifest, tmp_path) == ["missing artifact: summary.csv"]


def test_verify_flags_invalid_manifest(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.artifacts.append("ghost.csv")
    problems = verify_run_dir(manifest, tmp_path)
    assert any("invalid manifest" in p for p in problems)


def test_verify_anchor_skips_unanchored_manifest(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    assert manifest.anchor is None
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_dry_run_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_detects_manifest_body_edit(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.runner = "edited-after-anchoring"
    problems = verify_anchor(manifest, tmp_path)
    assert len(problems) == 1
    assert "anchored hash" in problems[0]


def test_verify_anchor_ots_proof_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    write_proof_for(manifest, tmp_path)
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_ots_missing_proof(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    problems = verify_anchor(manifest, tmp_path)
    assert problems == [f"missing anchor proof: {PROOF_NAME}"]


def test_verify_anchor_ots_proof_digest_mismatch(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    write_proof_for(manifest, tmp_path, digest_hex="ff" * 32)
    problems = verify_anchor(manifest, tmp_path)
    assert len(problems) == 1
    assert "proof commits to" in problems[0]
