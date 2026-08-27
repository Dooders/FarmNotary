"""Chain adapters.

Default is dry-run: return the payload that *would* be submitted.
The registry backend (farm_notary.registry.RegistryBackend) submits real
transactions and plugs in here without changing AgentFarm callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Tuple

from farm_notary.manifest import (
    MANIFEST_NAME,
    Manifest,
    build_manifest,
    write_manifest,
)


@dataclass
class AnchorReceipt:
    backend: str
    manifest_hash: str
    cid: Optional[str]
    tx_hash: Optional[str]
    dry_run: bool = True

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "manifest_hash": self.manifest_hash,
            "cid": self.cid,
            "tx_hash": self.tx_hash,
            "dry_run": self.dry_run,
        }


class AnchorBackend(Protocol):
    def submit(self, manifest: Manifest, *, cid: Optional[str] = None) -> AnchorReceipt:
        ...


class DryRunBackend:
    def submit(self, manifest: Manifest, *, cid: Optional[str] = None) -> AnchorReceipt:
        return AnchorReceipt(
            backend="dry-run",
            manifest_hash=manifest.content_hash(),
            cid=cid,
            tx_hash=None,
            dry_run=True,
        )


def get_backend(
    name: str,
    *,
    rpc_url: Optional[str] = None,
    contract: Optional[str] = None,
) -> AnchorBackend:
    if name == "dry-run":
        return DryRunBackend()
    if name == "registry":
        from farm_notary.registry import registry_backend_from_env

        return registry_backend_from_env(rpc_url=rpc_url, contract=contract)
    raise ValueError(f"unknown anchor backend {name!r} (expected dry-run or registry)")


def anchor_run(
    manifest: Manifest,
    *,
    cid: Optional[str] = None,
    backend: Optional[AnchorBackend] = None,
) -> AnchorReceipt:
    """Submit the manifest hash (and optional CID) and stamp the manifest."""
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


def notarize_run(
    run_dir: Path,
    *,
    config: Optional[Mapping[str, Any]] = None,
    git_sha: Optional[str] = None,
    runner: Optional[str] = None,
    official_record: Optional[Mapping[str, Any]] = None,
    backend: Optional[AnchorBackend] = None,
    pin: bool = False,
    ipfs_api: Optional[str] = None,
) -> Tuple[Manifest, AnchorReceipt]:
    """One-call hook for AgentFarm: manifest, optional pin, anchor, stamp.

    Builds and writes manifest.json for run_dir, optionally uploads the run
    directory to IPFS, anchors via the given backend (dry-run by default),
    and rewrites manifest.json with the cid and chain receipt.
    """
    run_dir = Path(run_dir)
    manifest = build_manifest(
        run_dir,
        config=config,
        git_sha=git_sha,
        runner=runner,
        official_record=official_record,
    )
    write_manifest(manifest, run_dir)

    cid = None
    if pin:
        from farm_notary.ipfs import IpfsClient

        client = IpfsClient(api_url=ipfs_api)
        cid = client.add_run_dir(run_dir, list(manifest.artifacts) + [MANIFEST_NAME])

    receipt = anchor_run(manifest, cid=cid, backend=backend)
    write_manifest(manifest, run_dir)
    return manifest, receipt
