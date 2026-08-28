"""Chain adapters.

Default is dry-run: return the payload that *would* be submitted.
The EAS backend (farm_notary.eas) submits a real attestation and needs the
``chain`` extra installed plus FARM_NOTARY_* environment configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from farm_notary.manifest import Manifest


@dataclass
class AnchorReceipt:
    backend: str
    manifest_hash: str
    cid: str | None
    tx_hash: str | None
    dry_run: bool = True
    attestation_uid: str | None = None
    chain_id: int | None = None


class AnchorBackend(Protocol):
    def submit(self, manifest: Manifest, *, cid: str | None = None) -> AnchorReceipt:
        ...


class DryRunBackend:
    def submit(self, manifest: Manifest, *, cid: str | None = None) -> AnchorReceipt:
        return AnchorReceipt(
            backend="dry-run",
            manifest_hash=manifest.content_hash(),
            cid=cid,
            tx_hash=None,
            dry_run=True,
        )


def get_backend(name: str) -> AnchorBackend:
    if name == "dry-run":
        return DryRunBackend()
    if name == "eas":
        from farm_notary.eas import EASBackend

        return EASBackend()
    raise ValueError(f"unknown anchor backend {name!r}")


def anchor_run(manifest: Manifest, *, cid: str | None = None, backend: AnchorBackend | None = None) -> AnchorReceipt:
    backend = backend or DryRunBackend()
    cid = cid or manifest.cid
    receipt = backend.submit(manifest, cid=cid)
    manifest.cid = cid
    manifest.chain = {
        "backend": receipt.backend,
        "manifest_hash": receipt.manifest_hash,
        "tx_hash": receipt.tx_hash,
        "attestation_uid": receipt.attestation_uid,
        "chain_id": receipt.chain_id,
        "dry_run": receipt.dry_run,
    }
    return receipt
