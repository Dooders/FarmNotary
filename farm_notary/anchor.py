"""Anchor adapters.

The anchoring layer is outsourced to existing public infrastructure; FarmNotary
only decides *what* gets anchored (the manifest content hash). Default is
dry-run: return the payload that *would* be submitted. The real backends are
OpenTimestamps (farm_notary.ots), which anchors into Bitcoin via free public
calendar servers, and EAS (farm_notary.eas), which anchors on Base / Base Sepolia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple

from farm_notary.manifest import (
    MANIFEST_NAME,
    Manifest,
    build_manifest,
    require_clean_identity,
    write_manifest,
)


@dataclass
class AnchorReceipt:
    backend: str
    manifest_hash: str
    cid: Optional[str]
    dry_run: bool = True
    attestation_uid: str | None = None
    chain_id: int | None = None
    tx_hash: str | None = None
    detail: dict = field(default_factory=dict)
    # Serialized proof to persist next to the manifest (e.g. manifest.ots).
    # Not part of to_dict: proofs are files, not manifest metadata.
    proof: Optional[bytes] = None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "manifest_hash": self.manifest_hash,
            "cid": self.cid,
            "dry_run": self.dry_run,
            "attestation_uid": self.attestation_uid,
            "chain_id": self.chain_id,
            "tx_hash": self.tx_hash,
            "detail": self.detail,
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
            dry_run=True,
        )


def get_backend(name: str, *, calendars=None) -> AnchorBackend:
    if name == "dry-run":
        return DryRunBackend()
    if name in ("ots", "opentimestamps"):
        from farm_notary.ots import OpenTimestampsBackend

        return OpenTimestampsBackend(calendars=calendars)
    if name == "eas":
        from farm_notary.eas import EASBackend

        return EASBackend()
    raise ValueError(f"unknown anchor backend {name!r} (expected dry-run, ots, or eas)")


def write_proof(receipt: AnchorReceipt, run_dir: Path) -> Optional[Path]:
    """Persist the receipt's proof file (if any) next to the manifest."""
    if receipt.proof is None:
        return None
    from farm_notary.ots import PROOF_NAME

    dest = Path(run_dir) / PROOF_NAME
    dest.write_bytes(receipt.proof)
    return dest


def anchor_run(
    manifest: Manifest,
    *,
    cid: Optional[str] = None,
    backend: Optional[AnchorBackend] = None,
    allow_dirty: bool = False,
) -> AnchorReceipt:
    """Submit the manifest hash (and optional CID) and stamp the manifest.

    Refuses a dirty tree by default: ``git_dirty`` means the SHA does not
    identify the code, so anchoring it is not a code-identity claim.
    Pass ``allow_dirty=True`` to record an explicit exception.
    """
    require_clean_identity(manifest.git_dirty, allow_dirty=allow_dirty)
    backend = backend or DryRunBackend()
    cid = cid or manifest.cid
    receipt = backend.submit(manifest, cid=cid)
    if cid is not None:
        manifest.cid = cid
    manifest.anchor = receipt.to_dict()
    return receipt


def notarize_run(
    run_dir: Path,
    *,
    publish_patterns: Optional[Sequence[str]] = None,
    publish_profile: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
    git_sha: Optional[str] = None,
    runner: Optional[str] = None,
    command: Optional[str] = None,
    lockfile: Optional[Path] = None,
    official_record: Optional[Mapping[str, Any]] = None,
    precommit_path: Optional[Path] = None,
    backend: Optional[AnchorBackend] = None,
    pin: bool = False,
    ipfs_api: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    allow_dirty: bool = False,
) -> Tuple[Manifest, AnchorReceipt]:
    """One-call hook for AgentFarm: manifest, optional pin, anchor, stamp.

    Builds and writes manifest.json for run_dir, optionally uploads the run
    directory to IPFS, anchors via the given backend (dry-run by default),
    persists any proof file, and rewrites manifest.json with the cid and
    anchor receipt.

    When *precommit_path* points to a ``precommit.json`` produced by
    ``farm-notary precommit``, its content hash is recorded in the manifest's
    ``precommit_hash`` field, binding the post-run proof to the pre-run
    specification.

    A dirty tree is refused unless *allow_dirty* is true: the SHA would not
    identify the code, so the anchor would not be a code-identity claim.
    """
    run_dir = Path(run_dir)
    manifest = build_manifest(
        run_dir,
        publish_patterns=publish_patterns,
        publish_profile=publish_profile,
        config=config,
        git_sha=git_sha,
        git_dirty=git_dirty,
        runner=runner,
        command=command,
        lockfile=lockfile,
        official_record=official_record,
        precommit_path=precommit_path,
    )
    require_clean_identity(manifest.git_dirty, allow_dirty=allow_dirty)
    write_manifest(manifest, run_dir)

    cid = None
    if pin:
        from farm_notary.ipfs import IpfsClient

        client = IpfsClient(api_url=ipfs_api)
        cid = client.add_run_dir(run_dir, list(manifest.artifacts) + [MANIFEST_NAME])

    receipt = anchor_run(manifest, cid=cid, backend=backend, allow_dirty=allow_dirty)
    write_proof(receipt, run_dir)
    write_manifest(manifest, run_dir)
    return manifest, receipt
