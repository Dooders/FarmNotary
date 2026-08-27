import json
from pathlib import Path

import pytest

from farm_notary.anchor import DryRunBackend, anchor_run, get_backend, notarize_run
from farm_notary.manifest import build_manifest, load_manifest


def make_run_dir(tmp_path: Path) -> Path:
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return tmp_path


def test_dry_run_receipt_and_stamp(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest = build_manifest(tmp_path, git_sha="abc")
    expected_hash = manifest.content_hash()

    receipt = anchor_run(manifest, cid="bafytest")

    assert receipt.dry_run is True
    assert receipt.backend == "dry-run"
    assert receipt.tx_hash is None
    assert receipt.manifest_hash == expected_hash
    assert manifest.cid == "bafytest"
    assert manifest.chain["manifest_hash"] == expected_hash
    # Stamping must not change what was anchored.
    assert manifest.content_hash() == expected_hash


def test_get_backend_dry_run_default_and_unknown():
    assert isinstance(get_backend("dry-run"), DryRunBackend)
    with pytest.raises(ValueError, match="unknown anchor backend"):
        get_backend("carrier-pigeon")


def test_notarize_run_writes_stamped_manifest(tmp_path: Path):
    make_run_dir(tmp_path)
    manifest, receipt = notarize_run(tmp_path, git_sha="abc", runner="consensus")

    on_disk = load_manifest(tmp_path)
    assert on_disk.chain["backend"] == "dry-run"
    assert on_disk.chain["manifest_hash"] == receipt.manifest_hash
    assert on_disk.content_hash() == receipt.manifest_hash
    assert on_disk.runner == "consensus"


def test_notarize_run_with_pin(monkeypatch, stub_server, tmp_path: Path):
    make_run_dir(tmp_path)
    monkeypatch.setenv("FARM_NOTARY_IPFS_API", stub_server.url)
    stub_server.response_body = (
        json.dumps({"Name": "", "Hash": "bafypinned"}) + "\n"
    ).encode()

    manifest, receipt = notarize_run(tmp_path, pin=True)

    assert receipt.cid == "bafypinned"
    assert load_manifest(tmp_path).cid == "bafypinned"
    # The pinned upload includes manifest.json alongside the artifacts.
    body = stub_server.requests[0]["body"]
    assert b'filename="manifest.json"' in body
    assert b'filename="summary.csv"' in body
