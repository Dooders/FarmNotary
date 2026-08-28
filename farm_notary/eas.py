"""EAS (Ethereum Attestation Service) anchor backend.

Submits ``(manifest_hash, cid)`` as an EAS attestation. EAS is an OP-stack
predeploy, so the contract addresses are identical on Base and Base Sepolia.

Requires the ``chain`` extra: ``pip install 'farm-notary[chain]'``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from eth_abi import encode as abi_encode
    from eth_account import Account
    from eth_utils import keccak, to_checksum_address
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "the EAS backend needs web3: pip install 'farm-notary[chain]'"
    ) from exc

from farm_notary.anchor import AnchorReceipt
from farm_notary.manifest import Manifest

# OP-stack predeploys, same address on Base and Base Sepolia.
EAS_ADDRESS = "0x4200000000000000000000000000000000000021"
SCHEMA_REGISTRY_ADDRESS = "0x4200000000000000000000000000000000000020"

FARM_NOTARY_SCHEMA = "bytes32 manifestHash,string cid"
ZERO_ADDRESS = "0x" + "00" * 20

# keccak256(schema ++ resolver ++ revocable) with zero resolver, non-revocable.
# EAS derives schema UIDs deterministically, so this is the same on every chain.
FARM_NOTARY_SCHEMA_UID = "0xc3d61e7073e9dcc59f65fe1a8a4bfd0b3e2c5fd2e32ad1c1d6c473fb1274ac08"

ATTESTED_TOPIC = "0x" + keccak(text="Attested(address,address,bytes32,bytes32)").hex()


@dataclass(frozen=True)
class ChainProfile:
    chain_id: int
    rpc_url: str
    easscan_url: str


CHAINS = {
    "base": ChainProfile(
        chain_id=8453,
        rpc_url="https://mainnet.base.org",
        easscan_url="https://base.easscan.org",
    ),
    "base-sepolia": ChainProfile(
        chain_id=84532,
        rpc_url="https://sepolia.base.org",
        easscan_url="https://base-sepolia.easscan.org",
    ),
}

EAS_ABI = [
    {
        "name": "attest",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "request",
                "type": "tuple",
                "components": [
                    {"name": "schema", "type": "bytes32"},
                    {
                        "name": "data",
                        "type": "tuple",
                        "components": [
                            {"name": "recipient", "type": "address"},
                            {"name": "expirationTime", "type": "uint64"},
                            {"name": "revocable", "type": "bool"},
                            {"name": "refUID", "type": "bytes32"},
                            {"name": "data", "type": "bytes"},
                            {"name": "value", "type": "uint256"},
                        ],
                    },
                ],
            }
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    }
]

SCHEMA_REGISTRY_ABI = [
    {
        "name": "register",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "schema", "type": "string"},
            {"name": "resolver", "type": "address"},
            {"name": "revocable", "type": "bool"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    }
]


@dataclass
class EASConfig:
    chain: str = "base-sepolia"
    rpc_url: str | None = None
    private_key: str | None = None
    schema_uid: str = FARM_NOTARY_SCHEMA_UID
    eas_address: str = EAS_ADDRESS

    @classmethod
    def from_env(cls) -> "EASConfig":
        chain = os.environ.get("FARM_NOTARY_CHAIN", "base-sepolia")
        if chain not in CHAINS:
            raise ValueError(f"unknown chain {chain!r}, expected one of {sorted(CHAINS)}")
        return cls(
            chain=chain,
            rpc_url=os.environ.get("FARM_NOTARY_RPC_URL") or CHAINS[chain].rpc_url,
            private_key=os.environ.get("FARM_NOTARY_PRIVATE_KEY"),
            schema_uid=os.environ.get("FARM_NOTARY_EAS_SCHEMA_UID", FARM_NOTARY_SCHEMA_UID),
            eas_address=os.environ.get("FARM_NOTARY_EAS_ADDRESS", EAS_ADDRESS),
        )

    @property
    def profile(self) -> ChainProfile:
        return CHAINS[self.chain]

    def require_key(self) -> str:
        if not self.private_key:
            raise ValueError("FARM_NOTARY_PRIVATE_KEY is not set; refusing to anchor")
        return self.private_key


def encode_attestation_data(manifest_hash: str, cid: str | None) -> bytes:
    """ABI-encode the attestation payload for the FarmNotary schema."""
    digest = bytes.fromhex(manifest_hash.removeprefix("0x"))
    if len(digest) != 32:
        raise ValueError(f"manifest hash must be 32 bytes, got {len(digest)}")
    return abi_encode(["bytes32", "string"], [digest, cid or ""])


def compute_schema_uid(
    schema: str = FARM_NOTARY_SCHEMA,
    resolver: str = ZERO_ADDRESS,
    revocable: bool = False,
) -> str:
    """Schema UID as EAS derives it: keccak256(schema ++ resolver ++ revocable)."""
    packed = (
        schema.encode("utf-8")
        + bytes.fromhex(resolver.removeprefix("0x"))
        + (b"\x01" if revocable else b"\x00")
    )
    return "0x" + keccak(packed).hex()


def parse_attestation_uid(logs: Iterable[Any], eas_address: str) -> str | None:
    """Pull the attestation UID out of the Attested event in a tx receipt."""
    eas = eas_address.lower()
    for log in logs:
        if str(log["address"]).lower() != eas:
            continue
        topics = log["topics"]
        topic0 = topics[0] if isinstance(topics[0], str) else "0x" + bytes(topics[0]).hex()
        if topic0.lower() != ATTESTED_TOPIC:
            continue
        data = log["data"]
        raw = bytes.fromhex(data.removeprefix("0x")) if isinstance(data, str) else bytes(data)
        return "0x" + raw[:32].hex()
    return None


def attestation_url(config: EASConfig, uid: str) -> str:
    return f"{config.profile.easscan_url}/attestation/view/{uid}"


def _raw_transaction(signed: Any) -> bytes:
    # eth-account renamed rawTransaction -> raw_transaction in newer releases.
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


class EASBackend:
    """Anchor backend that attests (manifest_hash, cid) via the EAS contract."""

    def __init__(self, config: EASConfig | None = None, w3: Any = None):
        self.config = config or EASConfig.from_env()
        self._w3 = w3

    def _connect(self) -> Any:
        if self._w3 is not None:
            return self._w3
        from web3 import HTTPProvider, Web3

        self._w3 = Web3(HTTPProvider(self.config.rpc_url))
        return self._w3

    def _send(self, contract_fn: Any, account: Any, w3: Any) -> Any:
        tx = contract_fn.build_transaction(
            {
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": self.config.profile.chain_id,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(_raw_transaction(signed))
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.get("status") == 0:
            raise RuntimeError(
                f"transaction {receipt['transactionHash'].hex()} was reverted (status=0)"
            )
        return receipt

    def submit(self, manifest: Manifest, *, cid: str | None = None) -> AnchorReceipt:
        config = self.config
        account = Account.from_key(config.require_key())
        w3 = self._connect()

        manifest_hash = manifest.content_hash()
        request = (
            bytes.fromhex(config.schema_uid.removeprefix("0x")),
            (
                to_checksum_address(ZERO_ADDRESS),  # recipient: none, public record
                0,  # expirationTime: never
                False,  # revocable: a notary record must not be revocable
                bytes(32),  # refUID
                encode_attestation_data(manifest_hash, cid),
                0,  # value
            ),
        )
        eas = w3.eth.contract(address=to_checksum_address(config.eas_address), abi=EAS_ABI)
        receipt = self._send(eas.functions.attest(request), account, w3)

        tx_hash = receipt["transactionHash"]
        tx_hex = tx_hash if isinstance(tx_hash, str) else "0x" + bytes(tx_hash).hex()
        attestation_uid = parse_attestation_uid(receipt["logs"], config.eas_address)
        if attestation_uid is None:
            raise RuntimeError(
                f"transaction {tx_hex} succeeded but no Attested event was found; "
                "check that FARM_NOTARY_EAS_ADDRESS points to a valid EAS contract"
            )
        return AnchorReceipt(
            backend="eas",
            manifest_hash=manifest_hash,
            cid=cid,
            tx_hash=tx_hex,
            dry_run=False,
            attestation_uid=attestation_uid,
            chain_id=config.profile.chain_id,
        )


def register_schema(config: EASConfig | None = None, w3: Any = None) -> str:
    """Register the FarmNotary schema with the EAS SchemaRegistry (once per chain).

    Returns the schema UID, which is deterministic, so re-running on a chain
    where the schema already exists reverts on-chain but the UID stays valid.
    """
    config = config or EASConfig.from_env()
    account = Account.from_key(config.require_key())
    backend = EASBackend(config, w3=w3)
    w3 = backend._connect()
    registry = w3.eth.contract(
        address=to_checksum_address(SCHEMA_REGISTRY_ADDRESS), abi=SCHEMA_REGISTRY_ABI
    )
    fn = registry.functions.register(FARM_NOTARY_SCHEMA, to_checksum_address(ZERO_ADDRESS), False)
    backend._send(fn, account, w3)
    return compute_schema_uid()
