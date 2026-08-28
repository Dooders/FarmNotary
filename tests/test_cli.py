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
            "--publish", "*.csv",
        ]
    ) == 0

    manifest = load_manifest(run_dir)
    assert manifest.git_sha == "abc"
    assert manifest.config == {"trials": 2}
    assert manifest.artifacts == ["summary.csv"]
    out = capsys.readouterr().out
    assert "content_hash" in out
    assert "unmatched" in out


def test_manifest_command_no_publish_returns_error(tmp_path: Path, capsys):
    """manifest fails with exit code 2 when no publish patterns are given."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc"]) == 2
    assert "publish patterns" in capsys.readouterr().err


def test_manifest_command_rejects_missing_dir(tmp_path: Path):
    assert main(["manifest", "--run-dir", str(tmp_path / "nope")]) == 2


def test_anchor_dry_run_updates_manifest(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    assert main(["anchor", "--run-dir", str(run_dir), "--allow-dirty"]) == 0

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
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "claim card" in out
    assert "tamper-evident record" in out
    assert "pass" in out
    assert "not claimed: scientific correctness" in out

    (run_dir / "summary.csv").write_text("tampered\n", encoding="utf-8")
    assert main(["verify", "--run-dir", str(run_dir)]) == 1
    out = capsys.readouterr().out
    assert "tamper-evident record" in out
    assert "fail" in out
    assert "hash mismatch" in out


def test_verify_via_manifest_path(tmp_path: Path):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    assert main(["verify", "--manifest", str(run_dir / "manifest.json")]) == 0


def test_verify_warns_and_continues_for_newer_schema(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest_data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest_data["schema"] = "farmnotary.manifest.v999"
    (run_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (run_dir / "summary.csv").write_text("tampered\n", encoding="utf-8")

    assert main(["verify", "--run-dir", str(run_dir)]) == 1
    captured = capsys.readouterr()
    out = captured.out
    assert "unsupported manifest schema" in out
    assert "artifact hash mismatch: summary.csv" in out
    assert "newer than this tool's known schema" in captured.err


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
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    monkeypatch.setattr(cli, "get_backend", lambda name, **_kw: FakeEASBackend())
    assert main(["anchor", "--run-dir", str(run_dir), "--backend", "eas", "--cid", "bafytest", "--allow-dirty"]) == 0
    capsys.readouterr()
    manifest = load_manifest(run_dir)
    assert manifest.cid == "bafytest"
    assert manifest.anchor["attestation_uid"] == "0x" + "22" * 32
    assert manifest.anchor["dry_run"] is False
    # The anchored hash matches what verify recomputes from the written manifest.
    assert main(["verify", "--run-dir", str(run_dir)]) == 0


def test_anchor_no_write(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    before = (run_dir / "manifest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "get_backend", lambda name, **_kw: FakeEASBackend())
    assert main(["anchor", "--run-dir", str(run_dir), "--backend", "eas", "--no-write", "--allow-dirty"]) == 0
    assert (run_dir / "manifest.json").read_text(encoding="utf-8") == before


def test_full_pin_anchor_upgrade_verify_flow(tmp_path: Path, monkeypatch, capsys):
    """manifest -> anchor --pin --backend ots -> verify (pending) -> upgrade -> verify (anchored)."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
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
                "--no-check-gateway",
                "--backend", "ots",
                "--calendar", calendar.url,
                "--allow-dirty",
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
        out = capsys.readouterr().out
        assert "existed by time T" in out
        assert "pending" in out

        # Still pending: calendar has no Bitcoin attestation yet.
        assert main(["upgrade", "--run-dir", str(run_dir)]) == 1
        capsys.readouterr()

        calendar.get_responses[f"/timestamp/{digest.hex()}"] = serialize_timestamp(
            bitcoin_timestamp(digest, 800000)
        )
        assert main(["upgrade", "--run-dir", str(run_dir)]) == 0
        assert "Bitcoin block 800000" in capsys.readouterr().out

        assert main(["verify", "--run-dir", str(run_dir)]) == 0
        out = capsys.readouterr().out
        assert "existed by time T" in out
        assert "Bitcoin height 800000" in out
    finally:
        ipfs.close()
        calendar.close()


def test_anchor_pin_gateway_reachable_recorded(tmp_path: Path, monkeypatch, capsys):
    """--pin with reachable gateway records cid_reachable=True in manifest."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    pre_anchor_hash = load_manifest(run_dir).content_hash()

    ipfs = StubServer()
    gateway = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafyreach"}) + "\n").encode()
        # Gateway returns 200 for this CID
        gateway.get_responses["/ipfs/bafyreach"] = b"data"
        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)

        import farm_notary.ipfs as _ipfs_mod
        monkeypatch.setattr(_ipfs_mod, "DEFAULT_GATEWAY_URL", gateway.url)

        assert main(["anchor", "--run-dir", str(run_dir), "--pin", "--allow-dirty"]) == 0

        manifest = load_manifest(run_dir)
        assert manifest.cid == "bafyreach"
        assert manifest.cid_reachable is True
        assert manifest.cid_reachable_checked_utc is not None
        assert manifest.content_hash() == pre_anchor_hash
    finally:
        ipfs.close()
        gateway.close()


def test_anchor_pin_gateway_unreachable_warns(tmp_path: Path, monkeypatch, capsys):
    """--pin with unreachable gateway records cid_reachable=False and warns."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0

    ipfs = StubServer()
    gateway = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafynope"}) + "\n").encode()
        # Gateway returns 404 (CID not registered in get_responses)
        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)

        import farm_notary.ipfs as _ipfs_mod
        monkeypatch.setattr(_ipfs_mod, "DEFAULT_GATEWAY_URL", gateway.url)

        assert main(["anchor", "--run-dir", str(run_dir), "--pin", "--allow-dirty"]) == 0

        manifest = load_manifest(run_dir)
        assert manifest.cid_reachable is False
        assert "warning" in capsys.readouterr().err
    finally:
        ipfs.close()
        gateway.close()


def test_anchor_pin_remote_delegates_to_kubo(tmp_path: Path, monkeypatch, capsys):
    """--pin-remote calls Kubo's remote-pin API after uploading."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0

    ipfs = StubServer()
    gateway = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafyremote"}) + "\n").encode()
        gateway.get_responses["/ipfs/bafyremote"] = b"data"

        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)
        import farm_notary.ipfs as _ipfs_mod
        monkeypatch.setattr(_ipfs_mod, "DEFAULT_GATEWAY_URL", gateway.url)

        # Monkeypatch pin_remote to return success and capture call
        from farm_notary.ipfs import IpfsClient
        pin_remote_calls = []

        def fake_pin_remote(self, cid, service, name=None):
            pin_remote_calls.append((cid, service, name))
            return {"Cid": cid, "Status": "queued"}

        monkeypatch.setattr(IpfsClient, "pin_remote", fake_pin_remote)

        assert main(["anchor", "--run-dir", str(run_dir), "--pin-remote", "pinata", "--allow-dirty"]) == 0

        manifest = load_manifest(run_dir)
        assert manifest.cid == "bafyremote"
        assert pin_remote_calls == [("bafyremote", "pinata", None)]
    finally:
        ipfs.close()
        gateway.close()


def test_anchor_no_check_gateway_skips_reachability(tmp_path: Path, monkeypatch):
    """--no-check-gateway skips the reachability check; cid_reachable stays None."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0

    ipfs = StubServer()
    try:
        ipfs.response_body = (json.dumps({"Name": "", "Hash": "bafyskip"}) + "\n").encode()
        monkeypatch.setenv("FARM_NOTARY_IPFS_API", ipfs.url)

        assert main(["anchor", "--run-dir", str(run_dir), "--pin", "--no-check-gateway", "--allow-dirty"]) == 0

        manifest = load_manifest(run_dir)
        assert manifest.cid == "bafyskip"
        assert manifest.cid_reachable is None
    finally:
        ipfs.close()
