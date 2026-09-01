"""Code identity is a claim: precommit and anchor refuse a dirty tree."""

import subprocess
from pathlib import Path

import pytest

from farm_notary.anchor import anchor_run, notarize_run
from farm_notary.cli import main
from farm_notary.manifest import (
    DirtyTreeError,
    build_manifest,
    load_manifest,
    require_clean_identity,
    write_manifest,
)
from farm_notary.precommit import PRECOMMIT_NAME, build_precommit


def init_repo(path: Path, *, dirty: bool = False) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()
    if dirty:
        (path / "f.txt").write_text("changed", encoding="utf-8")
    return sha


def test_build_precommit_refuses_dirty_tree(tmp_path: Path):
    sha = init_repo(tmp_path, dirty=True)
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        build_precommit(git_sha=sha, git_dirty=True)
    pc = build_precommit(git_sha=sha, git_dirty=True, allow_dirty=True)
    assert pc["git_dirty"] is True


def test_supplied_sha_still_detects_dirty_cwd(tmp_path: Path, monkeypatch):
    """Passing git_sha without git_dirty must not skip the dirty check."""
    repo = tmp_path / "repo"
    sha = init_repo(repo, dirty=True)
    monkeypatch.chdir(repo)
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        build_precommit(git_sha=sha)
    run_dir = repo / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    manifest = build_manifest(run_dir, publish_patterns=["*.csv"], git_sha=sha)
    assert manifest.git_dirty is True
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        anchor_run(manifest)


def test_require_clean_identity_detects_when_unset(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=True)
    monkeypatch.chdir(repo)
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        require_clean_identity(None)
    require_clean_identity(None, allow_dirty=True)


def test_require_clean_identity_refuses_dirty_repo_with_git_dirty_false(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=True)
    monkeypatch.chdir(repo)
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        require_clean_identity(False)
    require_clean_identity(False, allow_dirty=True)


def test_require_clean_identity_raises_on_recorded_dirty_flag_with_clean_repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=False)
    monkeypatch.chdir(repo)
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        require_clean_identity(True)
    require_clean_identity(True, allow_dirty=True)


def test_anchor_run_refuses_dirty_manifest(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=True
    )
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        anchor_run(manifest)
    receipt = anchor_run(manifest, allow_dirty=True)
    assert receipt.dry_run is True


def test_notarize_run_refuses_dirty_tree(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    with pytest.raises(DirtyTreeError, match="does not identify the code"):
        notarize_run(tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=True)
    manifest, _ = notarize_run(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=True,
        allow_dirty=True,
    )
    assert manifest.git_dirty is True


def test_cli_precommit_refuses_dirty_cwd(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=True)
    monkeypatch.chdir(repo)
    assert main(["precommit", "--command", "python run.py {run_dir}", "--out", str(repo)]) == 2
    err = capsys.readouterr().err
    assert "dirty" in err
    assert "--allow-dirty" in err
    assert not (repo / PRECOMMIT_NAME).is_file()


def test_cli_precommit_allow_dirty_records_the_flag(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    sha = init_repo(repo, dirty=True)
    monkeypatch.chdir(repo)
    assert (
        main(
            [
                "precommit",
                "--command", "python run.py {run_dir}",
                "--out", str(repo),
                "--allow-dirty",
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "dirty" in err
    from farm_notary.precommit import load_precommit

    pc = load_precommit(repo / PRECOMMIT_NAME)
    assert pc["git_dirty"] is True
    assert pc["git_sha"] == sha


def test_cli_precommit_clean_tree_does_not_need_flag(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=False)
    monkeypatch.chdir(repo)
    assert main(["precommit", "--command", "python run.py {run_dir}", "--out", str(repo)]) == 0
    from farm_notary.precommit import load_precommit

    pc = load_precommit(repo / PRECOMMIT_NAME)
    assert pc["git_dirty"] is False


def test_cli_anchor_refuses_dirty_manifest(tmp_path: Path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    manifest = build_manifest(
        run_dir, publish_patterns=["*.csv"], git_sha="abc", git_dirty=True
    )
    write_manifest(manifest, run_dir)
    assert main(["anchor", "--run-dir", str(run_dir)]) == 2
    err = capsys.readouterr().err
    assert "dirty" in err
    assert "--allow-dirty" in err
    assert load_manifest(run_dir).anchor is None


def test_cli_anchor_allow_dirty_stamps(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    manifest = build_manifest(
        run_dir, publish_patterns=["*.csv"], git_sha="abc", git_dirty=True
    )
    write_manifest(manifest, run_dir)
    assert main(["anchor", "--run-dir", str(run_dir), "--allow-dirty"]) == 0
    assert load_manifest(run_dir).anchor["backend"] == "dry-run"


def test_cli_manifest_records_cwd_dirty_then_anchor_refuses(
    tmp_path: Path, monkeypatch, capsys
):
    repo = tmp_path / "repo"
    init_repo(repo, dirty=True)
    run_dir = repo / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    assert load_manifest(run_dir).git_dirty is True
    capsys.readouterr()
    assert main(["anchor", "--run-dir", str(run_dir)]) == 2
    assert "dirty" in capsys.readouterr().err
