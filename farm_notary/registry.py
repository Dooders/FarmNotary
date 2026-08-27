"""SimulationRegistry access (contracts/SimulationRegistry.sol).

Reads use plain JSON-RPC ``eth_call`` over stdlib urllib, so verification
needs no dependencies beyond an RPC endpoint. Writes must sign transactions,
which requires web3: install ``farm-notary[chain]``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from farm_notary.keccak import function_selector
from farm_notary.manifest import Manifest

REGISTER_SIGNATURE = "register(bytes32,string)"
RECORDS_SIGNATURE = "records(bytes32)"

REGISTRY_ABI = [
    {
        "type": "function",
        "name": "register",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "manifestHash", "type": "bytes32"},
            {"name": "cid", "type": "string"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "records",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [
            {"name": "sender", "type": "address"},
            {"name": "cid", "type": "string"},
            {"name": "timestamp", "type": "uint256"},
        ],
    },
]


class RegistryError(RuntimeError):
    pass


@dataclass
class RegistryRecord:
    sender: str
    cid: str
    timestamp: int


def _hash_bytes(manifest_hash: str) -> bytes:
    raw = bytes.fromhex(manifest_hash.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError(f"manifest hash must be 32 bytes, got {len(raw)}")
    return raw


def encode_register_call(manifest_hash: str, cid: str) -> str:
    """ABI-encode ``register(bytes32,string)`` call data as a 0x-hex string."""
    cid_bytes = cid.encode("utf-8")
    padded_len = (len(cid_bytes) + 31) // 32 * 32
    data = (
        function_selector(REGISTER_SIGNATURE)
        + _hash_bytes(manifest_hash)
        + (0x40).to_bytes(32, "big")  # offset of the string argument
        + len(cid_bytes).to_bytes(32, "big")
        + cid_bytes.ljust(padded_len, b"\x00")
    )
    return "0x" + data.hex()


def encode_records_call(manifest_hash: str) -> str:
    """ABI-encode the ``records(bytes32)`` getter call data."""
    data = function_selector(RECORDS_SIGNATURE) + _hash_bytes(manifest_hash)
    return "0x" + data.hex()


def decode_records_result(result: str) -> Optional[RegistryRecord]:
    """Decode the (address, string, uint256) tuple; None if unregistered."""
    raw = bytes.fromhex(result.removeprefix("0x"))
    if len(raw) < 96:
        raise RegistryError(f"records() returned {len(raw)} bytes, expected >= 96")
    sender = "0x" + raw[12:32].hex()
    offset = int.from_bytes(raw[32:64], "big")
    timestamp = int.from_bytes(raw[64:96], "big")
    if timestamp == 0:
        return None
    cid_len = int.from_bytes(raw[offset:offset + 32], "big")
    cid = raw[offset + 32:offset + 32 + cid_len].decode("utf-8")
    return RegistryRecord(sender=sender, cid=cid, timestamp=timestamp)


def _rpc(rpc_url: str, method: str, params: list, timeout: float = 30.0) -> Any:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    request = urllib.request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise RegistryError(f"RPC request to {rpc_url} failed: {exc}") from exc
    if body.get("error"):
        raise RegistryError(f"RPC error: {body['error']}")
    return body.get("result")


def get_record(rpc_url: str, contract: str, manifest_hash: str) -> Optional[RegistryRecord]:
    """Look up a manifest hash on-chain. Returns None if never registered."""
    result = _rpc(
        rpc_url,
        "eth_call",
        [{"to": contract, "data": encode_records_call(manifest_hash)}, "latest"],
    )
    return decode_records_result(result)


class RegistryBackend:
    """Anchor backend that submits register(manifestHash, cid) transactions."""

    def __init__(self, rpc_url: str, contract: str, private_key: str):
        try:
            from web3 import Web3
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RegistryError(
                "the registry backend needs web3; install farm-notary[chain]"
            ) from exc
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._account = self._w3.eth.account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract), abi=REGISTRY_ABI
        )

    def submit(self, manifest: Manifest, *, cid: Optional[str] = None):
        from farm_notary.anchor import AnchorReceipt

        manifest_hash = manifest.content_hash()
        tx = self._contract.functions.register(
            _hash_bytes(manifest_hash), cid or ""
        ).build_transaction(
            {
                "from": self._account.address,
                "nonce": self._w3.eth.get_transaction_count(self._account.address),
            }
        )
        signed = self._account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = self._w3.eth.send_raw_transaction(raw)
        self._w3.eth.wait_for_transaction_receipt(tx_hash)
        return AnchorReceipt(
            backend="registry",
            manifest_hash=manifest_hash,
            cid=cid,
            tx_hash=tx_hash.hex(),
            dry_run=False,
        )


def registry_backend_from_env(
    rpc_url: Optional[str] = None,
    contract: Optional[str] = None,
    private_key_env: str = "FARM_NOTARY_PRIVATE_KEY",
) -> RegistryBackend:
    rpc_url = rpc_url or os.environ.get("FARM_NOTARY_RPC_URL")
    contract = contract or os.environ.get("FARM_NOTARY_CONTRACT")
    private_key = os.environ.get(private_key_env)
    missing = [
        name
        for name, value in (
            ("rpc url (--rpc-url or FARM_NOTARY_RPC_URL)", rpc_url),
            ("contract address (--contract or FARM_NOTARY_CONTRACT)", contract),
            (f"private key (env {private_key_env})", private_key),
        )
        if not value
    ]
    if missing:
        raise RegistryError("registry backend needs: " + "; ".join(missing))
    return RegistryBackend(rpc_url=rpc_url, contract=contract, private_key=private_key)
