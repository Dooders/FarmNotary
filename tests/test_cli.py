import json
from pathlib import Path

from farm_notary import cli
from farm_notary.anchor import AnchorReceipt
from farm_notary.cli import main
from farm_notary.manifest import load_manifest
from farm_notary.ots import PROOF_NAME
from tests.conftest import StubServer
from tests.test_ots import (
    bitcoin_timestamp,
    pending_timestamp,
    serialize_timestamp,
)


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    (run_dir / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")
    return run_dir


def test_manifest_command(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    config = tmp_path / "config.json"
    config.write_text('{"trials": 2}', encoding="utf-8")

    assert main(
        [
            "manifest",
            "--run-dir", str(run_dir),
            "--git-sha", "abc",
            "--runner", "consensus",
            "--config", str(config),
        ]
    ) == 0

    manifest = load_manifest(run_dir)
    assert manifest.git_sha == "abc"
    assert manifest.config == {"trials": 2}
    assert manifest.artifacts == ["summary.csv"]
    out = capsys.readouterr().out
    assert "content_hash" in out


def test_manifest_command_rejects_missing_dir(tmp_path: Path):
    assert main(["manifest", "--run-dir", str(tmp_path / "nope")]) == 2


def test_anchor_dry_run_updates_manifest(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    assert main(["anchor", "--run-dir", str(run_dir)]) == 0

    manifest = load_manifest(run_dir)
    assert manifest.anchor["backend"] == "dry-run"
    assert manifest.anchor["dry_run"] is True
    out = capsys.readouterr().out
    receipt = json.loads("{" + out.split("{", 1)[1])
    assert receipt["manifest_hash"] == manifest.content_hash()


def test_anchor_requires_manifest(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["anchor", "--run-dir", str(run_dir)]) == 2
    assert "manifest" in capsys.readouterr().err


def test_verify_ok_and_tamper(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    assert capsys.readouterr().out.count("OK") >= 1

    (run_dir / "summary.csv").write_text("tampered\n", encoding="utf-8")
    assert main(["verify", "--run-dir", str(run_dir)]) == 1
    assert "hash mismatch" in capsys.readouterr().out


def test_verify_via_manifest_path(tmp_path: Path):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir)]) == 0
    assert main(["verify", "--manifest", str(run_dir / "manifest.json")]) == 0


def test_upgrade_requires_proof(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["upgrade", "--run-dir", str(run_dir)]) == 2
    assert "anchor" in capsys.readouterr().err


class FakeEASBackend:
    def submit(self, manifest, *, cid=None):
        return AnchorReceipt(
            backend="eas",
            manifest_hash=manifest.content_hash(),
            cid=cid,
            dry_run=False,
            attestation_uid="0x" + "22" * 32,
            chain_id=84532,
        )


def test_anchor_eas_backend_writes_receipt(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    monkeypatch.setattr(cli, "get_backend", lambda name, **_kw: FakeEASBackend())
    assert main(["anchor", "--run-dir", str(run_dir), "--backend", "eas", "--cid", "bafytest"]) == 0
    capsys.readouterr()
    manifest = load_manifest(run_dir)
    assert manifest.cid == "bafytest"
    assert manifest.anchor["attestation_uid"] == "0x" + "22" * 32
    assert manifest.anchor["dry_run"] is False
    # The anchored hash matches what verify recomputes from the written manifest.
    assert main(["verify", "--run-dir", str(run_dir)]) == 0


def test_anchor_no_write(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    before = (run_dir / "manifest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "get_backend", lambda name, **_kw: FakeEASBackend())
    assert main(["anchor", "--run-dir", str(run_dir), "--backend", "eas", "--no-write"]) == 0
    assert (run_dir / "manifest.json").read_text(encoding="utf-8") == before


def test_full_pin_anchor_upgrade_verify_flow(tmp_path: Path, monkeypatch, capsys):
    """manifest -> anchor --pin --backend ots -> verify (pending) -> upgrade -> verify (anchored)."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    digest = bytes.fromhex(load_manifest(run_dir).content_hash())

    ipfs = StubServer()
    calendar = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafyroot"}) + "\n").encode()
        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)
        calendar.response_body = serialize_timestamp(
            pending_timestamp(digest, calendar.url)
        )

        assert main(
            [
                "anchor",
                "--run-dir", str(run_dir),
                "--pin",
                "--backend", "ots",
                "--calendar", calendar.url,
            ]
        ) == 0
        capsys.readouterr()

        manifest = load_manifest(run_dir)
        assert manifest.cid == "bafyroot"
        assert manifest.anchor["backend"] == "opentimestamps"
        assert (run_dir / PROOF_NAME).is_file()
        # Private artifacts never reach the pinned upload.
        assert b"votes_ballot" not in ipfs.requests[0]["body"]

        assert main(["verify", "--run-dir", str(run_dir)]) == 0
        assert "pending at calendar" in capsys.readouterr().out

        # Still pending: calendar has no Bitcoin attestation yet.
        assert main(["upgrade", "--run-dir", str(run_dir)]) == 1
        capsys.readouterr()

        calendar.get_responses[f"/timestamp/{digest.hex()}"] = serialize_timestamp(
            bitcoin_timestamp(digest, 800000)
        )
        assert main(["upgrade", "--run-dir", str(run_dir)]) == 0
        assert "Bitcoin block 800000" in capsys.readouterr().out

        assert main(["verify", "--run-dir", str(run_dir)]) == 0
        assert "anchored in Bitcoin block 800000" in capsys.readouterr().out
    finally:
        ipfs.close()
        calendar.close()
