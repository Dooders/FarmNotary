from pathlib import Path

from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.verify import verify_run_dir


def test_build_and_verify(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    (tmp_path / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, git_sha="abc", config={"trials": 2})
    assert "summary.csv" in manifest.artifact_hashes
    assert "votes_ballot.csv" not in manifest.artifact_hashes
    write_manifest(manifest, tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []
