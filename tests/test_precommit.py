"""Tests for the precommit module and the CLI precommit subcommand."""

import json
from pathlib import Path

import pytest

from farm_notary.anchor import notarize_run
from farm_notary.cli import main
from farm_notary.manifest import build_manifest, load_manifest, write_manifest
from farm_notary.precommit import (
    BOUND_FIELDS,
    PRECOMMIT_NAME,
    PRECOMMIT_VERSION,
    build_precommit,
    load_precommit,
    precommit_hash,
    write_precommit,
)
from farm_notary.verify import verify_precommit


# ---------------------------------------------------------------------------
# precommit module unit tests
# ---------------------------------------------------------------------------


def test_build_precommit_contains_expected_keys():
    pc = build_precommit(config={"trials": 5}, command="python run.py", git_sha="abc")
    assert pc["schema"] == PRECOMMIT_VERSION
    assert pc["config"] == {"trials": 5}
    assert pc["command"] == "python run.py"
    assert pc["git_sha"] == "abc"
    assert "created_utc" in pc


def test_precommit_hash_deterministic():
    pc = build_precommit(config={"x": 1}, command="cmd", git_sha="aaa")
    assert precommit_hash(pc) == precommit_hash(pc)


def test_precommit_hash_changes_on_mutation():
    pc = build_precommit(config={"x": 1}, command="cmd", git_sha="aaa")
    h1 = precommit_hash(pc)
    pc2 = dict(pc)
    pc2["command"] = "different"
    assert precommit_hash(pc2) != h1


def test_write_and_load_precommit(tmp_path):
    pc = build_precommit(config={"k": "v"}, command="go run .", git_sha="deadbeef")
    dest = tmp_path / PRECOMMIT_NAME
    write_precommit(pc, dest)
    loaded = load_precommit(dest)
    assert loaded == pc


def test_load_precommit_rejects_wrong_schema(tmp_path):
    bad = tmp_path / PRECOMMIT_NAME
    bad.write_text(json.dumps({"schema": "wrong.v99"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported precommit schema"):
        load_precommit(bad)


def test_load_precommit_rejects_non_object(tmp_path):
    bad = tmp_path / PRECOMMIT_NAME
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON object"):
        load_precommit(bad)


def test_precommit_with_lockfile(tmp_path):
    lf = tmp_path / "requirements.txt"
    lf.write_text("numpy==1.24.0\n", encoding="utf-8")
    pc = build_precommit(git_sha="abc", lockfile=lf)
    assert pc["lockfile"] == "requirements.txt"
    assert len(pc["lockfile_sha256"]) == 64


# ---------------------------------------------------------------------------
# verify_precommit unit tests
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return run_dir


def _make_manifest_with_precommit(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    pc = build_precommit(config={"trials": 3}, command="python run.py {run_dir}", git_sha="abc")
    pc_path = run_dir / PRECOMMIT_NAME
    write_precommit(pc, pc_path)
    manifest = build_manifest(
        run_dir, publish_patterns=["*.csv"], git_sha="abc", command="python run.py {run_dir}",
        config={"trials": 3}, precommit_path=pc_path,
    )
    return manifest, run_dir, pc


def test_verify_precommit_no_precommit_hash(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    manifest = build_manifest(run_dir, publish_patterns=["*.csv"], git_sha="abc")
    assert verify_precommit(manifest, run_dir) == []


def test_verify_precommit_ok(tmp_path):
    manifest, run_dir, _ = _make_manifest_with_precommit(tmp_path)
    assert verify_precommit(manifest, run_dir) == []


def test_verify_precommit_missing_file(tmp_path):
    manifest, run_dir, _ = _make_manifest_with_precommit(tmp_path)
    (run_dir / PRECOMMIT_NAME).unlink()
    problems = verify_precommit(manifest, run_dir)
    assert any("not found" in p for p in problems)


def test_verify_precommit_hash_mismatch(tmp_path):
    manifest, run_dir, pc = _make_manifest_with_precommit(tmp_path)
    tampered = dict(pc)
    tampered["command"] = "different"
    write_precommit(tampered, run_dir / PRECOMMIT_NAME)
    problems = verify_precommit(manifest, run_dir)
    assert any("hash mismatch" in p for p in problems)


def test_verify_precommit_field_mismatch(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    pc = build_precommit(config={"trials": 3}, command="python run.py {run_dir}", git_sha="abc")
    pc_path = run_dir / PRECOMMIT_NAME
    write_precommit(pc, pc_path)
    manifest = build_manifest(
        run_dir, publish_patterns=["*.csv"], git_sha="abc", command="python DIFFERENT.py {run_dir}",
        config={"trials": 3}, precommit_path=pc_path,
    )
    manifest.command = "python DIFFERENT.py {run_dir}"
    problems = verify_precommit(manifest, run_dir)
    assert any("command" in p for p in problems)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return run_dir


def test_precommit_cli_dry_run(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text('{"trials": 2}', encoding="utf-8")
    assert (
        main(
            [
                "precommit",
                "--config", str(config),
                "--command", "python run.py {run_dir}",
                "--git-sha", "deadbeef",
                "--out", str(tmp_path),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "precommit written to" in out
    assert "precommit_hash" in out

    pc_path = tmp_path / PRECOMMIT_NAME
    assert pc_path.is_file()
    pc = load_precommit(pc_path)
    assert pc["config"] == {"trials": 2}
    assert pc["command"] == "python run.py {run_dir}"
    assert pc["git_sha"] == "deadbeef"


def test_precommit_cli_hash_written(tmp_path, capsys):
    """precommit hash is printed and precommit.json exists."""
    assert (
        main(
            [
                "precommit",
                "--git-sha", "abc123",
                "--out", str(tmp_path),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    hash_line = [l for l in out.splitlines() if l.startswith("precommit_hash")]
    assert len(hash_line) == 1
    # Should be a 64-char hex string
    assert len(hash_line[0].split()[1]) == 64


def test_manifest_with_precommit_records_hash(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text('{"trials": 1}', encoding="utf-8")

    assert (
        main(
            [
                "precommit",
                "--config", str(config),
                "--command", "python run.py {run_dir}",
                "--git-sha", "aaa",
                "--out", str(run_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "manifest",
                "--run-dir", str(run_dir),
                "--git-sha", "aaa",
                "--command", "python run.py {run_dir}",
                "--config", str(config),
                "--publish", "*.csv",
                "--precommit", str(run_dir / PRECOMMIT_NAME),
            ]
        )
        == 0
    )
    capsys.readouterr()

    manifest = load_manifest(run_dir)
    assert manifest.precommit_hash is not None
    assert len(manifest.precommit_hash) == 64


def test_verify_with_precommit_ok(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text('{"trials": 1}', encoding="utf-8")

    main(
        [
            "precommit",
            "--config", str(config),
            "--command", "python run.py {run_dir}",
            "--git-sha", "aaa",
            "--out", str(run_dir),
        ]
    )
    main(
        [
            "manifest",
            "--run-dir", str(run_dir),
            "--git-sha", "aaa",
            "--command", "python run.py {run_dir}",
            "--config", str(config),
            "--publish", "*.csv",
            "--precommit", str(run_dir / PRECOMMIT_NAME),
        ]
    )
    capsys.readouterr()
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "pre-specified design" in out or "precommit present" in out


def test_verify_reports_precommit_mismatch(tmp_path, capsys):
    """If precommit.json is tampered, verify must fail."""
    run_dir = make_run_dir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text('{"trials": 1}', encoding="utf-8")

    main(
        [
            "precommit",
            "--config", str(config),
            "--command", "python run.py {run_dir}",
            "--git-sha", "aaa",
            "--out", str(run_dir),
        ]
    )
    main(
        [
            "manifest",
            "--run-dir", str(run_dir),
            "--git-sha", "aaa",
            "--command", "python run.py {run_dir}",
            "--config", str(config),
            "--publish", "*.csv",
            "--precommit", str(run_dir / PRECOMMIT_NAME),
        ]
    )
    capsys.readouterr()

    # Tamper with the precommit.json after the manifest has been written
    pc = load_precommit(run_dir / PRECOMMIT_NAME)
    pc["command"] = "python EVIL.py {run_dir}"
    write_precommit(pc, run_dir / PRECOMMIT_NAME)

    assert main(["verify", "--run-dir", str(run_dir)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "precommit" in out


def test_notarize_run_with_precommit(tmp_path):
    run_dir = make_run_dir(tmp_path)
    pc = build_precommit(config={"x": 1}, command="cmd {run_dir}", git_sha="abc")
    pc_path = run_dir / PRECOMMIT_NAME
    write_precommit(pc, pc_path)

    manifest, receipt = notarize_run(
        run_dir,
        publish_patterns=["*.csv"],
        config={"x": 1},
        command="cmd {run_dir}",
        git_sha="abc",
        precommit_path=pc_path,
    )
    assert manifest.precommit_hash is not None
    assert manifest.precommit_hash == precommit_hash(pc)
    assert verify_precommit(manifest, run_dir) == []
