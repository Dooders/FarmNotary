import json
from pathlib import Path

import pytest
from farm_notary.anchor import (
    DryRunBackend,
    anchor_run,
    get_backend,
    notarize_run,
    write_proof,
)
from farm_notary.manifest import build_manifest, load_manifest
from farm_notary.ots import (
    CID_BINDING_PROOF_NAME,
    PROOF_NAME,
    OpenTimestampsBackend,
    OtsError,
    deserialize_proof,
)

from tests.test_ots import pending_timestamp, serialize_timestamp


def make_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return tmp_path


def test_dry_run_receipt_and_stamp(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )
    expected_hash = manifest.content_hash()

    receipt = anchor_run(manifest, cid="bafytest")

    assert receipt.dry_run is True
    assert receipt.backend == "dry-run"
    assert receipt.proof is None
    assert receipt.manifest_hash == expected_hash
    assert manifest.cid == "bafytest"
    assert manifest.anchor["manifest_hash"] == expected_hash
    # Stamping must not change what was anchored.
    assert manifest.content_hash() == expected_hash


def test_get_backend_names():
    assert isinstance(get_backend("dry-run"), DryRunBackend)
    assert isinstance(get_backend("ots"), OpenTimestampsBackend)
    assert isinstance(get_backend("opentimestamps"), OpenTimestampsBackend)
    with pytest.raises(ValueError, match="unknown anchor backend"):
        get_backend("simulation-registry")


def test_notarize_run_writes_stamped_manifest(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest, receipt = notarize_run(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        runner="consensus",
    )

    on_disk = load_manifest(tmp_path)
    assert on_disk.anchor["backend"] == "dry-run"
    assert on_disk.anchor["manifest_hash"] == receipt.manifest_hash
    assert on_disk.content_hash() == receipt.manifest_hash
    assert on_disk.runner == "consensus"
    # Dry-run produces no proof file.
    assert not (tmp_path / PROOF_NAME).exists()


def test_notarize_run_with_pin(monkeypatch, stub_server, tmp_path: Path):
    make_run_dir(tmp_path)
    monkeypatch.setenv("FARM_NOTARY_IPFS_API", stub_server.url)
    stub_server.response_body = (
        json.dumps({"Name": "", "Hash": "bafypinned"}) + "\n"
    ).encode()

    manifest, receipt = notarize_run(
        tmp_path, publish_patterns=["*.csv"], pin=True, git_dirty=False
    )

    assert receipt.cid == "bafypinned"
    on_disk = load_manifest(tmp_path)
    assert on_disk.cid == "bafypinned"
    assert on_disk.pin_service == "local"
    # The pinned upload includes manifest.json alongside the artifacts.
    body = stub_server.requests[0]["body"]
    assert b'filename="manifest.json"' in body
    assert b'filename="summary.csv"' in body


def test_notarize_run_pin_does_not_upload_outside_symlink(monkeypatch, stub_server, tmp_path: Path):
    make_run_dir(tmp_path)
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("secret\n", encoding="utf-8")
    link = tmp_path / "outside_link.csv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    monkeypatch.setenv("FARM_NOTARY_IPFS_API", stub_server.url)
    stub_server.response_body = (
        json.dumps({"Name": "", "Hash": "bafypinned"}) + "\n"
    ).encode()

    manifest, receipt = notarize_run(
        tmp_path, publish_patterns=["*"], pin=True, git_dirty=False
    )

    assert receipt.cid == "bafypinned"
    assert "outside_link.csv" not in manifest.artifacts
    body = stub_server.requests[0]["body"]
    assert b'filename="outside_link.csv"' not in body


def test_notarize_run_with_ots_backend_writes_proof(stub_server, tmp_path: Path):
    make_run_dir(tmp_path)
    # The PendingAttestation serialization does not embed the digest, so any
    # placeholder is valid here; the proof will be rooted at the actual digest
    # submitted by notarize_run.
    placeholder = b"\xaa" * 32
    stub_server.response_body = serialize_timestamp(
        pending_timestamp(placeholder, stub_server.url)
    )

    backend = OpenTimestampsBackend(calendars=[stub_server.url])
    manifest, receipt = notarize_run(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        backend=backend,
    )

    expected_hash = receipt.manifest_hash
    assert manifest.content_hash() == expected_hash
    proof_path = tmp_path / PROOF_NAME
    assert proof_path.is_file()
    assert deserialize_proof(proof_path.read_bytes()).file_digest == bytes.fromhex(expected_hash)
    assert load_manifest(tmp_path).anchor["backend"] == "opentimestamps"


def test_notarize_run_warns_when_cid_binding_stamp_fails(
    monkeypatch, stub_server, tmp_path: Path, capsys
):
    make_run_dir(tmp_path)
    monkeypatch.setenv("FARM_NOTARY_IPFS_API", stub_server.url)
    stub_server.response_body = (
        json.dumps({"Name": "", "Hash": "bafypinned"}) + "\n"
    ).encode()

    calls = []

    def fake_stamp_digest(digest, calendars=None, timeout=10.0):
        calls.append(digest)
        if len(calls) == 1:
            return b"main proof", ["http://calendar.test"]
        raise OtsError("calendar unavailable")

    monkeypatch.setattr("farm_notary.ots.stamp_digest", fake_stamp_digest)
    backend = OpenTimestampsBackend(calendars=["http://calendar.test"])

    manifest, receipt = notarize_run(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        backend=backend,
        pin=True,
        allow_dirty=True,
    )

    assert manifest.cid == "bafypinned"
    assert receipt.cid == "bafypinned"
    assert len(calls) == 2
    err = capsys.readouterr().err
    assert "warning: CID binding proof could not be stamped" in err
    assert "calendar unavailable" in err
    assert (tmp_path / PROOF_NAME).is_file()
    assert not (tmp_path / CID_BINDING_PROOF_NAME).exists()
    on_disk = load_manifest(tmp_path)
    assert "cid_binding_proof" not in receipt.detail
    assert "cid_binding_proof" not in on_disk.anchor["detail"]


def test_write_proof_noop_without_proof(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )
    receipt = anchor_run(manifest)
    assert write_proof(receipt, tmp_path) is None
