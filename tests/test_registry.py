import json

import pytest

from farm_notary.registry import (
    RegistryError,
    decode_records_result,
    encode_records_call,
    encode_register_call,
    get_record,
)

HASH = "aa" * 32
SENDER = "00112233445566778899aabbccddeeff00112233"


def abi_encode_record(sender_hex: str, cid: str, timestamp: int) -> str:
    """Independent hand-rolled encoding of the records() return tuple."""
    cid_bytes = cid.encode()
    padded = cid_bytes + b"\x00" * (-len(cid_bytes) % 32)
    blob = (
        bytes(12) + bytes.fromhex(sender_hex)
        + (0x60).to_bytes(32, "big")
        + timestamp.to_bytes(32, "big")
        + len(cid_bytes).to_bytes(32, "big")
        + padded
    )
    return "0x" + blob.hex()


def test_encode_register_call_layout():
    data = bytes.fromhex(encode_register_call(HASH, "bafytest")[2:])
    assert data[:4].hex() == "cf2d31fb"
    assert data[4:36] == bytes.fromhex(HASH)
    assert int.from_bytes(data[36:68], "big") == 0x40
    assert int.from_bytes(data[68:100], "big") == 8
    assert data[100:132] == b"bafytest" + bytes(24)
    assert len(data) == 132


def test_encode_records_call_layout():
    data = bytes.fromhex(encode_records_call("0x" + HASH)[2:])
    assert data[:4].hex() == "01e64725"
    assert data[4:] == bytes.fromhex(HASH)


def test_encode_rejects_wrong_hash_length():
    with pytest.raises(ValueError, match="32 bytes"):
        encode_records_call("aabb")


def test_decode_records_result():
    record = decode_records_result(abi_encode_record(SENDER, "bafytest", 1700000000))
    assert record.sender == "0x" + SENDER
    assert record.cid == "bafytest"
    assert record.timestamp == 1700000000


def test_decode_unregistered_returns_none():
    assert decode_records_result(abi_encode_record(SENDER, "", 0)) is None
    assert decode_records_result("0x" + "00" * 96) is None


def test_decode_rejects_short_result():
    with pytest.raises(RegistryError, match="expected"):
        decode_records_result("0x1234")


def test_get_record_round_trip(stub_server):
    stub_server.response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": abi_encode_record(SENDER, "bafytest", 42)}
    ).encode()
    record = get_record(stub_server.url, "0x" + "cc" * 20, HASH)
    assert record.cid == "bafytest"
    assert record.timestamp == 42

    request = json.loads(stub_server.requests[0]["body"])
    assert request["method"] == "eth_call"
    assert request["params"][0]["to"] == "0x" + "cc" * 20
    assert request["params"][0]["data"] == encode_records_call(HASH)
    assert request["params"][1] == "latest"


def test_get_record_surfaces_rpc_error(stub_server):
    stub_server.response_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
    ).encode()
    with pytest.raises(RegistryError, match="boom"):
        get_record(stub_server.url, "0x" + "cc" * 20, HASH)


def test_registry_backend_import_error_without_web3():
    try:
        import web3  # noqa: F401

        pytest.skip("web3 installed; import-error path not reachable")
    except ImportError:
        pass
    from farm_notary.registry import RegistryBackend

    with pytest.raises(RegistryError, match=r"farm-notary\[chain\]"):
        RegistryBackend("http://127.0.0.1:1", "0x" + "cc" * 20, "00" * 32)
