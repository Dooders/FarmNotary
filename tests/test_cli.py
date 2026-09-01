import json
from pathlib import Path

from farm_notary import cli
from farm_notary.anchor import AnchorReceipt
from farm_notary.cli import main
from farm_notary.manifest import load_manifest
from farm_notary.ots import (
    CID_BINDING_PROOF_NAME,
    DEFAULT_CALENDARS,
    PROOF_NAME,
    cid_binding_digest,
    serialize_proof,
)
from tests.conftest import StubServer
from tests.test_ots import (
    bitcoin_timestamp,
    mixed_pending_timestamp,
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
    err = capsys.readouterr().err
    assert "publish patterns" in err
    assert "--profile" in err


def test_manifest_command_profile_consensus(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "REPORT.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "notes.txt").write_text("scratch\n", encoding="utf-8")
    assert main(
        ["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--profile", "consensus"]
    ) == 0
    out = capsys.readouterr().out
    assert "profile consensus" in out
    manifest = load_manifest(run_dir)
    assert manifest.publish_profile == "consensus"
    assert "REPORT.md" in manifest.artifacts
    assert "summary.csv" in manifest.artifacts
    assert "notes.txt" not in manifest.artifacts
    assert "REPORT.md" in manifest.publish_patterns


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


def test_verify_requires_a_target(tmp_path: Path, capsys):
    assert main(["verify"]) == 2
    assert "--run-dir" in capsys.readouterr().err


def test_index_requires_run_or_campaign(tmp_path: Path, capsys):
    assert main(["index", "--registry", str(tmp_path / "reg")]) == 2
    assert "--run-dir" in capsys.readouterr().err


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
        assert (run_dir / CID_BINDING_PROOF_NAME).is_file()
        assert manifest.anchor["detail"]["cid_binding_proof"] == CID_BINDING_PROOF_NAME
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
        cid_digest = cid_binding_digest(manifest.content_hash(), manifest.cid)
        calendar.get_responses[f"/timestamp/{cid_digest.hex()}"] = serialize_timestamp(
            bitcoin_timestamp(cid_digest, 800001)
        )
        assert main(["upgrade", "--run-dir", str(run_dir)]) == 0
        out = capsys.readouterr().out
        assert "manifest.ots: anchored in Bitcoin block 800000" in out
        assert "manifest.cid.ots: anchored in Bitcoin block 800001" in out

        assert main(["verify", "--run-dir", str(run_dir)]) == 0
        out = capsys.readouterr().out
        assert "existed by time T" in out
        assert "Bitcoin height 800000" in out
    finally:
        ipfs.close()
        calendar.close()


def test_verify_distinguishes_public_and_user_supplied_pending_calendars(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--git-sha", "abc", "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    digest = bytes.fromhex(manifest.content_hash())
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {
        "backend": "opentimestamps",
        "manifest_hash": manifest.content_hash(),
        "detail": {"proof": PROOF_NAME},
    }
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    (run_dir / PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, DEFAULT_CALENDARS[0]))
    )
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "pending on public OpenTimestamps calendar:" in out
    assert DEFAULT_CALENDARS[0] in out

    (run_dir / PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, "https://example.com"))
    )
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert (
        "•  existed by time T             — pending at user-supplied calendar: "
        "https://example.com (unverified claim; untrusted until Bitcoin)"
    ) in out

    (run_dir / PROOF_NAME).write_bytes(
        serialize_proof(
            mixed_pending_timestamp(digest, DEFAULT_CALENDARS[0], "https://example.com")
        )
    )
    assert main(["verify", "--run-dir", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert (
        "•  existed by time T             — pending on public OpenTimestamps calendar: "
        f"{DEFAULT_CALENDARS[0]} (unverified claim); user-supplied calendar: https://example.com "
        "(unverified claim; untrusted until Bitcoin)"
    ) in out


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
        assert manifest.pin_service == "local"
        assert manifest.cid_reachable is True
        assert manifest.cid_reachable_checked_utc is not None
        assert manifest.content_hash() == pre_anchor_hash
        assert "not archival" in capsys.readouterr().err
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
        assert manifest.pin_service == "pinata"
        assert pin_remote_calls == [("bafyremote", "pinata", None)]
        assert "not archival" not in capsys.readouterr().err
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


# ---------------------------------------------------------------------------
# check subcommand
# ---------------------------------------------------------------------------

def test_check_basic(tmp_path: Path, capsys):
    """check prints content_hash and claim_level from manifest only."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "content_hash:" in out
    assert "claim_level:" in out
    assert "anchor:       missing" in out


def test_check_with_cid(tmp_path: Path, capsys):
    """check prints cid when present in the manifest."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["cid"] = "bafytest"
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert main(["check", "--manifest", str(run_dir / "manifest.json")]) == 0
    out = capsys.readouterr().out
    assert "cid:" in out
    assert "bafytest" in out


def test_check_anchor_hash_mismatch(tmp_path: Path, capsys):
    """check exits 1 when anchored hash disagrees with content hash."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {"backend": "dry-run", "manifest_hash": "sha256:deadbeef"}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_check_ots_anchor_no_proof_file(tmp_path: Path, capsys):
    """check reports missing proof file for ots backend without error exit."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    content_hash = manifest.content_hash()
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {"backend": "opentimestamps", "manifest_hash": content_hash}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    # Either "proof file missing" or "OTS proof present but farm-notary[ots] not installed"
    assert "anchor:" in out


def test_check_ots_anchor_escapes_untrusted_proof_name(tmp_path: Path, capsys):
    """check escapes newline/control characters in manifest proof names."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    content_hash = manifest.content_hash()
    malicious_name = "bad\nproof"
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {
        "backend": "opentimestamps",
        "manifest_hash": content_hash,
        "detail": {"proof": malicious_name},
    }
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"proof file missing ({malicious_name!r})" in out


def test_check_ots_proof_present_without_ots_dependency(tmp_path: Path, capsys, monkeypatch):
    """check exits 0 when proof exists but farm-notary[ots] is unavailable."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    content_hash = manifest.content_hash()
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {"backend": "opentimestamps", "manifest_hash": content_hash}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (run_dir / PROOF_NAME).write_bytes(b"dummy-proof")
    monkeypatch.setattr(
        "farm_notary.ots.verify_proof",
        lambda *_: [
            "OpenTimestamps anchoring needs the opentimestamps library; install farm-notary[ots]"
        ],
    )

    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OTS proof present but farm-notary[ots] not installed" in out


def test_check_ots_proof_import_error_without_ots_dependency(tmp_path: Path, capsys, monkeypatch):
    """check exits 0 when lazy OTS imports fail during proof verification."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    content_hash = manifest.content_hash()
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {"backend": "opentimestamps", "manifest_hash": content_hash}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (run_dir / PROOF_NAME).write_bytes(b"dummy-proof")
    monkeypatch.setattr(
        "farm_notary.ots.verify_proof",
        lambda *_: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'opentimestamps'")),
    )

    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OTS proof present but farm-notary[ots] not installed" in out


def test_check_with_ots_proof(tmp_path: Path, capsys):
    """check verifies OTS proof when farm-notary[ots] is installed."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    manifest = load_manifest(run_dir)
    content_hash = manifest.content_hash()

    from tests.test_ots import bitcoin_timestamp
    from farm_notary.ots import serialize_proof, PROOF_NAME

    digest = bytes.fromhex(content_hash)
    ts = bitcoin_timestamp(digest, 840000)
    proof_bytes = serialize_proof(ts)
    (run_dir / PROOF_NAME).write_bytes(proof_bytes)

    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["anchor"] = {"backend": "opentimestamps", "manifest_hash": content_hash}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bitcoin height" in out


def test_check_identity_displayed(tmp_path: Path, capsys):
    """check prints identity scheme and principal when present."""
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    data["identity"] = {"scheme": "ssh", "principal": "lab@example.com", "signature": "fakesig"}
    (run_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    rc = main(["check", "--manifest", str(run_dir / "manifest.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "identity:" in out
    assert "lab@example.com" in out
    assert "not validated" in out


def test_check_missing_manifest(tmp_path: Path, capsys):
    """check exits 2 when the manifest file does not exist."""
    rc = main(["check", "--manifest", str(tmp_path / "no_such.json")])
    assert rc == 2
    assert "error" in capsys.readouterr().err


def test_check_rejects_invalid_manifest(tmp_path: Path, capsys):
    """check exits 2 for invalid manifests instead of coercing defaults."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    rc = main(["check", "--manifest", str(manifest_path)])
    assert rc == 2
    assert "error: could not load manifest" in capsys.readouterr().err


def test_check_rejects_invalid_manifest_field_types(tmp_path: Path, capsys):
    """check exits 2 when key manifest fields have invalid types."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "farmnotary.manifest.v1",
                "created_utc": "2026-01-01T00:00:00Z",
                "artifacts": None,
                "artifact_hashes": {},
                "publish_patterns": ["*.csv"],
                "unmatched_count": 0,
                "anchor": "invalid",
            }
        ),
        encoding="utf-8",
    )
    rc = main(["check", "--manifest", str(manifest_path)])
    assert rc == 2
    assert "error: could not load manifest" in capsys.readouterr().err


def test_emit_interop_writes_unsigned_files(tmp_path: Path, capsys):
    from farm_notary.interop import C2PA_FILE_NAME, SLSA_FILE_NAME

    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    capsys.readouterr()
    assert main(["emit-interop", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert SLSA_FILE_NAME in out
    assert C2PA_FILE_NAME in out
    assert (run_dir / SLSA_FILE_NAME).is_file()
    assert (run_dir / C2PA_FILE_NAME).is_file()
    data = json.loads((run_dir / SLSA_FILE_NAME).read_text(encoding="utf-8"))
    assert data["farmnotary_interop"]["status"] == "unsigned-summary-not-for-verification"


def test_archive_zenodo_requires_creator(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    token_path = tmp_path / "zenodo-token"
    token_path.write_text("tok\n", encoding="utf-8")
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    capsys.readouterr()
    rc = main(
        ["archive", str(run_dir), "--zenodo", "--zenodo-token", f"@{token_path}"]
    )
    assert rc == 2
    assert "--zenodo-creator is required" in capsys.readouterr().err


def test_archive_zenodo_rejects_raw_token_on_argv(tmp_path: Path, capsys):
    run_dir = make_run_dir(tmp_path)
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    capsys.readouterr()

    rc = main(
        [
            "archive",
            str(run_dir),
            "--zenodo",
            "--zenodo-token",
            "tok",
            "--zenodo-creator",
            "Test Author",
        ]
    )

    assert rc == 1
    assert "--zenodo-token must be @PATH" in capsys.readouterr().err


def test_archive_zenodo_reads_token_from_file(tmp_path: Path, capsys, monkeypatch):
    run_dir = make_run_dir(tmp_path)
    token_path = tmp_path / "zenodo-token"
    token_path.write_text("tok\n", encoding="utf-8")
    assert main(["manifest", "--run-dir", str(run_dir), "--publish", "*.csv"]) == 0
    capsys.readouterr()
    captured = {}

    def deposit_manifest(*args, **kwargs):
        captured["token"] = kwargs["token"]
        return {"id": 123}

    monkeypatch.setattr("farm_notary.archive.deposit_manifest", deposit_manifest)

    assert main(
        [
            "archive",
            str(run_dir),
            "--zenodo",
            "--zenodo-token",
            f"@{token_path}",
            "--zenodo-creator",
            "Test Author",
        ]
    ) == 0
    assert captured["token"] == "tok"


def test_chain_cli_relative_paths_verify(tmp_path: Path, capsys):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "summary.csv").write_text("a\n", encoding="utf-8")
    (b / "summary.csv").write_text("b\n", encoding="utf-8")
    assert main(["manifest", "--run-dir", str(a), "--publish", "*.csv"]) == 0
    assert main(["manifest", "--run-dir", str(b), "--publish", "*.csv"]) == 0
    capsys.readouterr()
    assert main(["chain", str(a), str(b)]) == 0
    assert main(["chain", "--verify", "--chain-file", str(b / "provenance-chain.json")]) == 0
    out = capsys.readouterr().out
    assert "ok: chain" in out
