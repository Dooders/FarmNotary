from pathlib import Path

import pytest

from farm_notary.anchor import anchor_run
from farm_notary.eas import (
    ATTESTED_TOPIC,
    EAS_ADDRESS,
    FARM_NOTARY_SCHEMA,
    FARM_NOTARY_SCHEMA_UID,
    SCHEMA_REGISTRY_ADDRESS,
    EASBackend,
    EASConfig,
    attestation_url,
    compute_schema_uid,
    encode_attestation_data,
    parse_attestation_uid,
    register_schema,
)
from farm_notary.manifest import build_manifest

# Well-known throwaway key (hardhat account 0); never fund it.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class StubFn:
    def __init__(self, contract, name, args):
        self.contract = contract
        self.name = name
        self.args = args

    def build_transaction(self, params):
        self.contract.calls.append((self.name, self.args, params))
        return {
            "chainId": params["chainId"],
            "nonce": params["nonce"],
            "maxFeePerGas": 10**9,
            "maxPriorityFeePerGas": 10**9,
            "gas": 300_000,
            "to": self.contract.address,
            "value": 0,
            "data": b"",
        }


class StubFunctions:
    def __init__(self, contract):
        self._contract = contract

    def __getattr__(self, name):
        return lambda *args: StubFn(self._contract, name, args)


class StubContract:
    def __init__(self, address, abi):
        self.address = address
        self.abi = abi
        self.calls = []
        self.functions = StubFunctions(self)


class StubEth:
    def __init__(self, parent):
        self._parent = parent

    def contract(self, address, abi):
        contract = StubContract(address, abi)
        self._parent.contracts.append(contract)
        return contract

    def get_transaction_count(self, address):
        return 7

    def send_raw_transaction(self, raw):
        self._parent.sent.append(raw)
        return b"\x11" * 32

    def wait_for_transaction_receipt(self, tx_hash):
        return {"transactionHash": tx_hash, "logs": self._parent.logs}


class StubW3:
    def __init__(self, logs=()):
        self.contracts = []
        self.sent = []
        self.logs = list(logs)
        self.eth = StubEth(self)


def attested_log(uid: str) -> dict:
    return {
        "address": EAS_ADDRESS,
        "topics": [ATTESTED_TOPIC, "0x" + "00" * 32, "0x" + "00" * 32, "0x" + "00" * 32],
        "data": uid,
    }


def test_encode_attestation_data_known_vector():
    encoded = encode_attestation_data("ab" * 32, "bafytest")
    assert encoded.hex() == (
        "ab" * 32
        + "0000000000000000000000000000000000000000000000000000000000000040"
        + "0000000000000000000000000000000000000000000000000000000000000008"
        + "6261667974657374000000000000000000000000000000000000000000000000"
    )


def test_encode_attestation_data_accepts_prefix_and_no_cid():
    assert encode_attestation_data("0x" + "ab" * 32, None) == encode_attestation_data("ab" * 32, "")


def test_encode_attestation_data_rejects_bad_hash():
    with pytest.raises(ValueError):
        encode_attestation_data("abcd", "cid")


def test_compute_schema_uid_matches_pinned_constant():
    assert compute_schema_uid() == FARM_NOTARY_SCHEMA_UID
    assert compute_schema_uid(revocable=True) != FARM_NOTARY_SCHEMA_UID


def test_parse_attestation_uid():
    uid = "0x" + "22" * 32
    assert parse_attestation_uid([attested_log(uid)], EAS_ADDRESS) == uid
    # Logs from other contracts or other events are ignored.
    other = dict(attested_log(uid), address="0x" + "99" * 20)
    assert parse_attestation_uid([other], EAS_ADDRESS) is None
    wrong_topic = dict(attested_log(uid), topics=["0x" + "aa" * 32])
    assert parse_attestation_uid([wrong_topic], EAS_ADDRESS) is None
    # Bytes-typed topics and data, as returned by live web3 providers.
    raw = {
        "address": EAS_ADDRESS,
        "topics": [bytes.fromhex(ATTESTED_TOPIC[2:])],
        "data": bytes.fromhex("22" * 32),
    }
    assert parse_attestation_uid([raw], EAS_ADDRESS) == uid


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("FARM_NOTARY_CHAIN", "base")
    monkeypatch.setenv("FARM_NOTARY_PRIVATE_KEY", TEST_KEY)
    monkeypatch.setenv("FARM_NOTARY_RPC_URL", "http://localhost:8545")
    config = EASConfig.from_env()
    assert config.profile.chain_id == 8453
    assert config.rpc_url == "http://localhost:8545"
    assert config.schema_uid == FARM_NOTARY_SCHEMA_UID
    assert config.require_key() == TEST_KEY


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("FARM_NOTARY_CHAIN", raising=False)
    monkeypatch.delenv("FARM_NOTARY_RPC_URL", raising=False)
    config = EASConfig.from_env()
    assert config.chain == "base-sepolia"
    assert config.rpc_url == "https://sepolia.base.org"


def test_config_rejects_unknown_chain(monkeypatch):
    monkeypatch.setenv("FARM_NOTARY_CHAIN", "mainnet")
    with pytest.raises(ValueError):
        EASConfig.from_env()


def test_submit_requires_private_key(tmp_path: Path):
    manifest = build_manifest(tmp_path, git_sha="abc")
    backend = EASBackend(EASConfig(private_key=None), w3=StubW3())
    with pytest.raises(ValueError):
        backend.submit(manifest)


def test_submit_builds_attestation_and_receipt(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, git_sha="abc", config={"trials": 2})
    uid = "0x" + "22" * 32
    w3 = StubW3(logs=[attested_log(uid)])
    backend = EASBackend(EASConfig(private_key=TEST_KEY), w3=w3)

    receipt = anchor_run(manifest, cid="bafytest", backend=backend)

    assert receipt.backend == "eas"
    assert receipt.dry_run is False
    assert receipt.manifest_hash == manifest.content_hash()
    assert receipt.tx_hash == "0x" + "11" * 32
    assert receipt.attestation_uid == uid
    assert receipt.chain_id == 84532
    assert manifest.cid == "bafytest"
    assert manifest.chain["attestation_uid"] == uid
    assert manifest.chain["dry_run"] is False

    name, args, params = w3.contracts[0].calls[0]
    assert name == "attest"
    assert params["nonce"] == 7
    assert params["chainId"] == 84532
    schema_uid, data = args[0]
    assert schema_uid == bytes.fromhex(FARM_NOTARY_SCHEMA_UID[2:])
    recipient, expiration, revocable, ref_uid, payload, value = data
    assert int(recipient, 16) == 0
    assert expiration == 0
    assert revocable is False
    assert ref_uid == bytes(32)
    assert payload == encode_attestation_data(manifest.content_hash(), "bafytest")
    assert value == 0
    assert len(w3.sent) == 1  # one signed raw transaction went out


def test_register_schema_uses_registry(tmp_path: Path):
    w3 = StubW3()
    uid = register_schema(EASConfig(private_key=TEST_KEY), w3=w3)
    assert uid == FARM_NOTARY_SCHEMA_UID
    contract = w3.contracts[0]
    assert contract.address.lower() == SCHEMA_REGISTRY_ADDRESS
    name, args, _ = contract.calls[0]
    assert name == "register"
    assert args[0] == FARM_NOTARY_SCHEMA
    assert int(args[1], 16) == 0
    assert args[2] is False


def test_attestation_url():
    config = EASConfig(chain="base")
    uid = "0x" + "22" * 32
    assert attestation_url(config, uid) == f"https://base.easscan.org/attestation/view/{uid}"
