"""Chain adapters.

Default is dry-run: return the payload that *would* be submitted.
Real EAS / RPC backends plug in here without changing AgentFarm callers.
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


def anchor_run(manifest: Manifest, *, cid: str | None = None, backend: AnchorBackend | None = None) -> AnchorReceipt:
    backend = backend or DryRunBackend()
    receipt = backend.submit(manifest, cid=cid)
    manifest.cid = cid
    manifest.chain = {
        "backend": receipt.backend,
        "manifest_hash": receipt.manifest_hash,
        "tx_hash": receipt.tx_hash,
        "dry_run": receipt.dry_run,
    }
    return receipt
