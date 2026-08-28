"""Verification: rehash local artifacts, then check the anchor proof.

Both checks return a list of human-readable problems; empty means verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

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


def verify_receipt(manifest: Manifest, run_dir: Path) -> List[str]:
    """Check the reproduction receipt (if present) against the manifest.

    Validates that the receipt refers to this manifest's content hash and,
    when a reproduction.ots proof exists, that the proof commits to the
    receipt's own hash.
    """
    from farm_notary.manifest import RECEIPT_NAME
    from farm_notary.reproduce import RECEIPT_PROOF_NAME, load_receipt, receipt_hash

    problems: List[str] = []
    run_dir = Path(run_dir)
    receipt_path = run_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        return problems
    try:
        receipt = load_receipt(run_dir)
    except (ValueError, OSError) as exc:
        problems.append(f"could not load reproduction receipt: {exc}")
        return problems
    manifest_hash = manifest.content_hash()
    if receipt.get("original_manifest_hash") != manifest_hash:
        problems.append(
            f"reproduction receipt refers to {receipt.get('original_manifest_hash')}, "
            f"manifest content hash is {manifest_hash}"
        )
    if not receipt.get("ok"):
        problems.append("reproduction receipt records a failed reproduction")
    proof_path = run_dir / RECEIPT_PROOF_NAME
    if proof_path.is_file():
        from farm_notary.ots import verify_proof

        problems += [
            f"receipt proof: {p}"
            for p in verify_proof(proof_path.read_bytes(), receipt_hash(receipt))
        ]
    return problems


def verify_anchor(manifest: Manifest, run_dir: Path) -> List[str]:
    """Check the manifest's anchor receipt against the recomputed hash.

    For OpenTimestamps anchors this also validates that the proof file next
    to the manifest commits to the recomputed manifest content hash.
    """
    problems: List[str] = []
    if manifest.anchor is None:
        return problems
    manifest_hash = manifest.content_hash()
    anchored_hash = manifest.anchor.get("manifest_hash")
    if anchored_hash != manifest_hash:
        problems.append(
            f"anchored hash {anchored_hash} does not match manifest content hash {manifest_hash}"
        )
    if manifest.anchor.get("backend") == "opentimestamps":
        from farm_notary.ots import PROOF_NAME, verify_proof

        proof_path = Path(run_dir) / manifest.anchor.get("detail", {}).get(
            "proof", PROOF_NAME
        )
        if not proof_path.is_file():
            problems.append(f"missing anchor proof: {proof_path.name}")
        else:
            problems += verify_proof(proof_path.read_bytes(), manifest_hash)
    return problems


def verify_precommit(manifest: Manifest, run_dir: Path) -> List[str]:
    """Verify the precommit binding (if present).

    Checks that:
    - ``precommit.json`` exists next to the manifest.
    - Its content hash matches ``manifest.precommit_hash``.
    - The config, command, and git_sha recorded in the precommit match the
      manifest byte-for-byte (the fields that were meant to be pre-specified).
    - When ``precommit.ots`` is present, the proof commits to the precommit
      content hash.

    Returns an empty list when no precommit is referenced by the manifest.
    """
    from farm_notary.precommit import (
        BOUND_FIELDS,
        PRECOMMIT_NAME,
        PRECOMMIT_PROOF_NAME,
        load_precommit,
        precommit_hash,
    )

    problems: List[str] = []
    if manifest.precommit_hash is None:
        return problems

    run_dir = Path(run_dir)
    pc_path = run_dir / PRECOMMIT_NAME
    if not pc_path.is_file():
        problems.append(f"precommit_hash present in manifest but {PRECOMMIT_NAME} not found")
        return problems

    try:
        pc = load_precommit(pc_path)
    except (ValueError, OSError) as exc:
        problems.append(f"could not load precommit: {exc}")
        return problems

    computed = precommit_hash(pc)
    if computed != manifest.precommit_hash:
        problems.append(
            f"precommit hash mismatch: manifest records {manifest.precommit_hash}, "
            f"computed {computed}"
        )

    for field_name in BOUND_FIELDS:
        pc_val = pc.get(field_name)
        manifest_val = getattr(manifest, field_name, None)
        if pc_val != manifest_val:
            problems.append(
                f"precommit/{field_name} mismatch: precommit={pc_val!r}, "
                f"manifest={manifest_val!r}"
            )

    proof_path = run_dir / PRECOMMIT_PROOF_NAME
    if proof_path.is_file():
        from farm_notary.ots import verify_proof

        problems += [
            f"precommit proof: {p}"
            for p in verify_proof(proof_path.read_bytes(), computed)
        ]

    return problems

