import json
import urllib.parse
from pathlib import Path

import pytest

from farm_notary.ipfs import IpfsClient, IpfsError, check_gateway_reachability


def kubo_add_response(entries) -> bytes:
    return ("\n".join(json.dumps(e) for e in entries) + "\n").encode()


def test_add_run_dir_returns_wrap_directory_cid(stub_server, tmp_path: Path):
    (tmp_path / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "round_1.json").write_text("{}", encoding="utf-8")
    stub_server.response_body = kubo_add_response(
        [
            {"Name": "summary.csv", "Hash": "bafyfile1", "Size": "10"},
            {"Name": "metrics/round_1.json", "Hash": "bafyfile2", "Size": "4"},
            {"Name": "", "Hash": "bafyroot", "Size": "20"},
        ]
    )

    client = IpfsClient(api_url=stub_server.url)
    cid = client.add_run_dir(tmp_path, ["summary.csv", "metrics/round_1.json"])
    assert cid == "bafyroot"

    request = stub_server.requests[0]
    path, _, query = request["path"].partition("?")
    assert path == "/api/v0/add"
    params = urllib.parse.parse_qs(query)
    assert params["wrap-with-directory"] == ["true"]
    assert params["pin"] == ["true"]
    assert params["cid-version"] == ["1"]

    body = request["body"]
    assert b'filename="summary.csv"' in body
    # Nested paths are URL-quoted inside the filename.
    assert b'filename="metrics%2Fround_1.json"' in body
    assert b"a,b\n1,2\n" in body


def test_unreachable_daemon_raises_helpful_error(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("x\n", encoding="utf-8")
    client = IpfsClient(api_url="http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(IpfsError, match="unreachable"):
        client.add_run_dir(tmp_path, ["summary.csv"])


def test_empty_upload_rejected():
    with pytest.raises(IpfsError, match="nothing to upload"):
        IpfsClient(api_url="http://127.0.0.1:1").add_files([])


def test_api_url_from_environment(monkeypatch, stub_server, tmp_path: Path):
    (tmp_path / "summary.csv").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("FARM_NOTARY_IPFS_API", stub_server.url)
    stub_server.response_body = kubo_add_response([{"Name": "", "Hash": "bafyenv"}])
    assert IpfsClient().add_run_dir(tmp_path, ["summary.csv"]) == "bafyenv"


def test_check_gateway_reachability_true(stub_server):
    stub_server.get_responses["/ipfs/bafytest"] = b"data"
    assert check_gateway_reachability("bafytest", gateway_url=stub_server.url, timeout=5) is True
    req = stub_server.requests[-1]
    assert req["method"] == "HEAD"
    assert req["path"] == "/ipfs/bafytest"


def test_check_gateway_reachability_false_on_404(stub_server):
    # No entry for this CID → 404
    assert check_gateway_reachability("bafymissing", gateway_url=stub_server.url, timeout=5) is False


def test_check_gateway_reachability_false_on_network_error():
    assert check_gateway_reachability("bafyx", gateway_url="http://127.0.0.1:1", timeout=0.3) is False


def test_pin_remote_calls_kubo_endpoint(stub_server):
    stub_server.response_body = json.dumps({"Cid": "bafytest", "Name": "myrun", "Status": "queued"}).encode()
    client = IpfsClient(api_url=stub_server.url)
    result = client.pin_remote("bafytest", "pinata", name="myrun")
    assert result["Cid"] == "bafytest"

    req = stub_server.requests[-1]
    path, _, query = req["path"].partition("?")
    assert path == "/api/v0/pin/remote/add"
    params = urllib.parse.parse_qs(query)
    assert params["arg"] == ["bafytest"]
    assert params["service"] == ["pinata"]
    assert params["name"] == ["myrun"]


def test_pin_remote_raises_on_network_error():
    client = IpfsClient(api_url="http://127.0.0.1:1", timeout=0.3)
    with pytest.raises(IpfsError, match="remote-pin"):
        client.pin_remote("bafyx", "myservice")
