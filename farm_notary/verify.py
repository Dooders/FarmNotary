"""Verification: rehash local artifacts, then optionally match the chain.

Both checks return a list of human-readable problems; empty means verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from farm_notary.manifest import Manifest, hash_file


def verify_run_dir(manifest: Manifest, run_dir: Path) -> List[str]:
    """Rehash every artifact in the manifest against the run directory."""
    problems: List[str] = []
    run_dir = Path(run_dir)
    try:
        manifest.validate()
    except ValueError as exc:
        problems.append(f"invalid manifest: {exc}")
    for name, expected in sorted(manifest.artifact_hashes.items()):
        path = run_dir / name
        if not path.is_file():
            problems.append(f"missing artifact: {name}")
            continue
        actual = hash_file(path)
        if actual != expected:
            problems.append(f"hash mismatch: {name}")
    return problems


def verify_chain(
    manifest: Manifest,
    *,
    rpc_url: str,
    contract: str,
    expected_cid: Optional[str] = None,
) -> List[str]:
    """Check that the manifest's content hash is registered on-chain.

    If expected_cid is not given, the manifest's own cid field is used
    (when set) to cross-check the CID stored with the on-chain record.
    """
    from farm_notary.registry import RegistryError, get_record

    problems: List[str] = []
    manifest_hash = manifest.content_hash()
    try:
        record = get_record(rpc_url, contract, manifest_hash)
    except RegistryError as exc:
        return [f"chain lookup failed: {exc}"]
    if record is None:
        return [f"manifest hash {manifest_hash} not registered at {contract}"]
    expected_cid = expected_cid or manifest.cid
    if expected_cid and record.cid != expected_cid:
        problems.append(
            f"cid mismatch: chain has {record.cid!r}, expected {expected_cid!r}"
        )
    return problems
