import shutil
import subprocess
from pathlib import Path

import pytest

from farm_notary.cli import main
from farm_notary.identity import (
    IdentityError,
    sign_content_hash,
    sign_record,
    verify_identity,
)
from farm_notary.manifest import build_manifest, write_manifest


def _ssh_key(tmp_path: Path) -> Path:
    key = tmp_path / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-C", "lab@example"],
        check=True,
        capture_output=True,
    )
    return key


def _manifest(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(run, publish_patterns=["*.csv"], git_sha="abc", config={"seed": 0})
    write_manifest(manifest, run)
    return run, manifest


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_ssh_sign_and_verify(tmp_path):
    key = _ssh_key(tmp_path)
    digest = "ab" * 32
    identity = sign_content_hash(digest, scheme="ssh", key_path=key, principal="lab@example")
    assert identity["scheme"] == "ssh"
    assert identity["principal"] == "lab@example"
    assert "BEGIN SSH SIGNATURE" in identity["signature"]
    assert verify_identity(identity, digest) == []


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_ssh_reject_wrong_hash(tmp_path):
    key = _ssh_key(tmp_path)
    identity = sign_content_hash("ab" * 32, scheme="ssh", key_path=key)
    problems = verify_identity(identity, "cd" * 32)
    assert problems
    assert any("content hash" in p or "identity" in p for p in problems)


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_sign_does_not_change_content_hash(tmp_path):
    run, manifest = _manifest(tmp_path)
    before = manifest.content_hash()
    sign_record(manifest, scheme="ssh", key_path=_ssh_key(tmp_path), principal="lab@example")
    assert manifest.identity["scheme"] == "ssh"
    assert manifest.content_hash() == before
    write_manifest(manifest, run)
    assert verify_identity(manifest.identity, manifest.content_hash()) == []


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_cli_sign_and_verify(tmp_path, capsys):
    run, _ = _manifest(tmp_path)
    key = _ssh_key(tmp_path)
    assert main(["sign", "--run-dir", str(run), "--scheme", "ssh", "--key", str(key), "--principal", "lab@example"]) == 0
    capsys.readouterr()
    assert main(["verify", "--run-dir", str(run)]) == 0
    out = capsys.readouterr().out
    assert "identity" in out
    assert "lab@example" in out


def test_unknown_scheme():
    with pytest.raises(IdentityError, match="unknown"):
        sign_content_hash("ab" * 32, scheme="eas", key_path=Path("/dev/null"))


def test_verify_identity_absent_is_ok():
    assert verify_identity(None, "ab" * 32) == []
