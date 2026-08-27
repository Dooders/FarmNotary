import json
from pathlib import Path

from farm_notary.cli import main
from farm_notary.manifest import load_manifest
from tests.conftest import StubServer
from tests.test_registry import SENDER, abi_encode_record

CONTRACT = "0x" + "cc" * 20


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


def test_manifest_command_rejects_missing_dir(tmp_path: Path, capsys):
    assert main(["manifest", "--run-dir", str(tmp_path / "nope")]) == 2


def test_anchor_dry_run_updates_manifest(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0
    assert main(["anchor", "--run-dir", str(run_dir)]) == 0

    manifest = load_manifest(run_dir)
    assert manifest.chain["backend"] == "dry-run"
    assert manifest.chain["dry_run"] is True
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


def test_verify_chain_requires_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("FARM_NOTARY_RPC_URL", raising=False)
    monkeypatch.delenv("FARM_NOTARY_CONTRACT", raising=False)
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir)]) == 0
    assert main(["verify", "--run-dir", str(run_dir), "--chain"]) == 2


def test_full_pin_and_chain_flow(tmp_path: Path, monkeypatch, capsys):
    """manifest -> anchor --pin (stub IPFS) -> verify --chain (stub RPC)."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 0

    ipfs = StubServer()
    rpc = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafyroot"}) + "\n").encode()
        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)
        assert main(["anchor", "--run-dir", str(run_dir), "--pin"]) == 0

        manifest = load_manifest(run_dir)
        assert manifest.cid == "bafyroot"
        # Private artifacts never reach the pinned upload.
        assert b"votes_ballot" not in ipfs.requests[0]["body"]

        rpc.response_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": abi_encode_record(SENDER, "bafyroot", 42),
            }
        ).encode()
        assert main(
            [
                "verify",
                "--run-dir", str(run_dir),
                "--chain",
                "--rpc-url", rpc.url,
                "--contract", CONTRACT,
            ]
        ) == 0
        assert "OK" in capsys.readouterr().out
    finally:
        ipfs.close()
        rpc.close()
