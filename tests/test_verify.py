import json
from pathlib import Path

from farm_notary.manifest import build_manifest
from farm_notary.verify import verify_chain, verify_run_dir
from tests.test_registry import SENDER, abi_encode_record

CONTRACT = "0x" + "cc" * 20


def make_manifest(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return build_manifest(tmp_path, git_sha="abc")


def test_verify_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []


def test_verify_detects_tampering(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.9\n", encoding="utf-8")
    assert verify_run_dir(manifest, tmp_path) == ["hash mismatch: summary.csv"]


def test_verify_detects_missing_artifact(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").unlink()
    assert verify_run_dir(manifest, tmp_path) == ["missing artifact: summary.csv"]


def test_verify_flags_invalid_manifest(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.artifacts.append("ghost.csv")
    problems = verify_run_dir(manifest, tmp_path)
    assert any("invalid manifest" in p for p in problems)


def test_verify_chain_match(stub_server, tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.cid = "bafytest"
    stub_server.response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": abi_encode_record(SENDER, "bafytest", 42)}
    ).encode()
    assert verify_chain(manifest, rpc_url=stub_server.url, contract=CONTRACT) == []


def test_verify_chain_unregistered(stub_server, tmp_path: Path):
    manifest = make_manifest(tmp_path)
    stub_server.response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": "0x" + "00" * 96}
    ).encode()
    problems = verify_chain(manifest, rpc_url=stub_server.url, contract=CONTRACT)
    assert problems == [
        f"manifest hash {manifest.content_hash()} not registered at {CONTRACT}"
    ]


def test_verify_chain_cid_mismatch(stub_server, tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.cid = "bafyexpected"
    stub_server.response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": abi_encode_record(SENDER, "bafyother", 42)}
    ).encode()
    problems = verify_chain(manifest, rpc_url=stub_server.url, contract=CONTRACT)
    assert problems == ["cid mismatch: chain has 'bafyother', expected 'bafyexpected'"]


def test_verify_chain_unreachable_rpc(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    problems = verify_chain(manifest, rpc_url="http://127.0.0.1:1", contract=CONTRACT)
    assert len(problems) == 1
    assert problems[0].startswith("chain lookup failed")
