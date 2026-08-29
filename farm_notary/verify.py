"""Verification: rehash local artifacts, then check the anchor proof.

Low-level checks return a list of human-readable problems; empty means that
check passed. ``evaluate_claims`` turns those checks into a CLAIMS.md claim
card so a reviewer reads earned claims, not exit codes.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

from farm_notary.beacon import BeaconCheck, verify_beacon_binding
from farm_notary.ladder import LadderResult, evaluate_ladder
from farm_notary.manifest import RECEIPT_NAME, Manifest, hash_file
from farm_notary.precommit import PRECOMMIT_NAME, PRECOMMIT_PROOF_NAME, load_precommit
from farm_notary.schema import MANIFEST_VERSION


def verify_derived_artifacts(manifest: Manifest, run_dir: Path, *, allow_execute: bool = False) -> List[str]:
    """Run ``derived_from`` rules if the manifest (or profile) declares them.

    ``allow_execute`` must be *True* to permit running manifest-supplied
    commands; the default *False* protects against executing untrusted input
    from downloaded manifests.
    """
    from farm_notary.derive import verify_derived

    return verify_derived(manifest, run_dir, allow_execute=allow_execute)


def verify_identity_record(record, _run_dir: Path) -> List[str]:
    """Check an optional minisign / SSH signature of the content hash."""
    from farm_notary.identity import verify_identity

    return verify_identity(getattr(record, "identity", None), record.content_hash())


def _schema_version_number(schema: str) -> int:
    """Extract the integer version from 'farmnotary.manifest.vN'. Returns 0 for unrecognised strings."""
    prefix = "farmnotary.manifest.v"
    if schema.startswith(prefix):
        try:
            return int(schema[len(prefix):])
        except ValueError:
            pass
    return 0


def verify_run_dir(manifest: Manifest, run_dir: Path) -> List[str]:
    """Rehash every artifact in the manifest against the run directory."""
    problems: List[str] = []
    run_dir = Path(run_dir)

    # Warn early when the manifest was produced by a newer tool version so
    # callers know the check may miss schema fields introduced after this
    # release, but still attempt verification.
    if manifest.schema != MANIFEST_VERSION:
        current_v = _schema_version_number(MANIFEST_VERSION)
        manifest_v = _schema_version_number(manifest.schema)
        if manifest_v > 0 and manifest_v > current_v:
            warnings.warn(
                f"manifest schema {manifest.schema!r} is newer than this tool's "
                f"known schema {MANIFEST_VERSION!r}; upgrade farm-notary for full verification.",
                stacklevel=2,
            )

    try:
        manifest.validate()
    except ValueError as exc:
        problems.append(f"invalid manifest: {exc}")
    cid_hint = f" (fetch with: ipfs get {manifest.cid})" if manifest.cid else ""
    for name, expected in sorted(manifest.artifact_hashes.items()):
        path = run_dir / name
        if not path.is_file():
            problems.append(f"artifact unreachable: {name}{cid_hint}")
            continue
        actual = hash_file(path)
        if actual != expected:
            problems.append(f"artifact hash mismatch: {name}")
    return problems


def verify_receipt(manifest: Manifest, run_dir: Path) -> List[str]:
    """Check the reproduction receipt (if present) against the manifest.

    Validates that the receipt refers to this manifest's content hash and,
    when a reproduction.ots proof exists, that the proof commits to the
    receipt's own hash.  When a ``"sigstore"`` bundle is present and
    ``cosign`` is on PATH, the bundle is verified against the receipt bytes;
    a failed verification is reported as a problem.  When ``cosign`` is not
    available the bundle is noted but not treated as a hard failure so that
    unsigned-receipt verification keeps working without cosign installed.
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
    # Check the Sigstore bundle when cosign is available.
    sigstore_bundle = receipt.get("sigstore")
    if sigstore_bundle:
        from farm_notary.sigstore import cosign_available, verify_sigstore_bundle

        if cosign_available():
            sig_probs, _ = verify_sigstore_bundle(sigstore_bundle, receipt)
            problems += [f"sigstore: {p}" for p in sig_probs]
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
      manifest. With a ``seed_plan``, config is compared without seed keys
      (the concrete seed is derived after the plan).
    - When ``precommit.ots`` is present, the proof commits to the precommit
      content hash.

    Returns an empty list when no precommit is referenced by the manifest.
    """
    from farm_notary.beacon import config_has_seed, strip_seed_keys
    from farm_notary.precommit import (
        BOUND_FIELDS,
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

    seed_plan = pc.get("seed_plan")
    if seed_plan and config_has_seed(pc.get("config")):
        problems.append(
            "precommit config must not contain a seed when seed_plan is set"
        )

    for field_name in BOUND_FIELDS:
        import json as _json

        pc_val = pc.get(field_name)
        manifest_val = getattr(manifest, field_name, None)
        if field_name == "config" and seed_plan:
            pc_val = strip_seed_keys(pc_val if isinstance(pc_val, dict) else {})
            manifest_val = strip_seed_keys(
                manifest_val if isinstance(manifest_val, dict) else {}
            )
        # Use canonical JSON comparison to avoid Python equality quirks such as
        # ``True == 1`` or ``1 == 1.0`` that would let a semantically-changed
        # value pass as matching.
        def _canonical(v: object) -> str:
            return _json.dumps(v, sort_keys=True, separators=(",", ":"))

        if _canonical(pc_val) != _canonical(manifest_val):
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


# ---------------------------------------------------------------------------
# Claim card — the human interface of `farm-notary verify`
# ---------------------------------------------------------------------------

_CLAIM_NAMES = (
    "tamper-evident record",
    "existed by time T",
    "pre-specified design",
    "bitwise reproducible (scoped)",
)


@dataclass
class ClaimCard:
    """One status per CLAIMS.md claim, plus the diagnostic problem list.

    Missing is not failure: it means the claim was not earned. ``problems``
    is empty only when every check that *was* attempted passed. ``ladder``
    is the stacked L0–L3 shorthand; it never changes exit status.
    """

    tamper_evident: str
    existed_by: str
    pre_specified: str
    bitwise_reproducible: str
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    ladder: Optional[LadderResult] = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> List[str]:
        width = max(len(name) for name in _CLAIM_NAMES)
        rows = (
            ("tamper-evident record", self.tamper_evident),
            ("existed by time T", self.existed_by),
            ("pre-specified design", self.pre_specified),
            ("bitwise reproducible (scoped)", self.bitwise_reproducible),
        )
        out = ["claim card"]
        if self.ladder is not None:
            out.extend(self.ladder.lines())
        for name, status in rows:
            out.append(f"•  {name:<{width}} — {status}")
        out.append("•  not claimed: scientific correctness")
        return out

    def render(self) -> str:
        return "\n".join(self.lines()) + "\n"


def _existed_by_status(
    manifest: Manifest, run_dir: Path, anchor_problems: List[str]
) -> str:
    """CLAIMS.md 'existed by time T': pending, Bitcoin height, missing, or fail."""
    if manifest.anchor is None or manifest.anchor.get("backend") != "opentimestamps":
        return "missing"
    if anchor_problems:
        return "fail"
    from farm_notary.ots import PROOF_NAME, proof_status

    proof_path = Path(run_dir) / manifest.anchor.get("detail", {}).get(
        "proof", PROOF_NAME
    )
    if not proof_path.is_file():
        return "fail"
    try:
        status = proof_status(proof_path.read_bytes())
    except Exception:
        return "fail"
    if status.bitcoin_heights:
        return f"Bitcoin height {min(status.bitcoin_heights)}"
    if status.pending_calendars:
        return "pending"
    return "fail"


def _pre_specified_status(manifest: Manifest, precommit_problems: List[str]) -> str:
    """CLAIMS.md 'pre-specified design': precommit bound, missing, or fail."""
    if manifest.precommit_hash is None:
        return "missing"
    if precommit_problems:
        return "fail"
    return "precommit bound"


def _bitwise_status(
    manifest: Manifest, run_dir: Path, receipt_problems: List[str]
) -> str:
    """CLAIMS.md 'bitwise reproducible (scoped)': N/M plus the allowed sentence."""
    from farm_notary.reproduce import load_receipt
    from farm_notary.scope import format_bitwise_status

    receipt_path = Path(run_dir) / RECEIPT_NAME
    if not receipt_path.is_file():
        return "missing"
    try:
        receipt = load_receipt(run_dir)
    except (ValueError, OSError):
        return "fail"
    if receipt.get("original_manifest_hash") != manifest.content_hash():
        return "fail"

    matched = list(receipt.get("matched") or [])
    mismatched = list(receipt.get("mismatched") or [])
    missing = list(receipt.get("missing") or [])
    ignored_files = list(receipt.get("ignored") or [])
    ignore_globs = [str(g) for g in (receipt.get("ignore") or []) if g]
    compared = len(matched) + len(mismatched) + len(missing)
    if compared == 0:
        return "missing"
    score = f"{len(matched)}/{compared}"
    ignored = ignore_globs or ignored_files
    if ignored:
        score = f"{score}, ignored: {', '.join(ignored)}"
    ok = not receipt_problems and bool(receipt.get("ok"))
    return format_bitwise_status(
        score, receipt.get("environment") or {}, ok=ok
    )


def evaluate_claims(
    manifest: Manifest,
    run_dir: Path,
    *,
    beacon_client: Optional[Any] = None,
    precommit: Optional[Any] = None,
) -> ClaimCard:
    """Run every verify check and return a CLAIMS.md claim card.

    The card is always complete: a missing precommit or receipt is reported
    as ``missing``, not as a problem. Problems are reserved for checks that
    were attempted and failed. A beacon fetch failure leaves L2 unearned
    without failing verify. Live HTTP comparison is opt-in (``beacon_client``).
    """
    run_dir = Path(run_dir)
    tamper_problems = verify_run_dir(manifest, run_dir)
    anchor_problems = verify_anchor(manifest, run_dir)
    receipt_problems = verify_receipt(manifest, run_dir)
    precommit_problems = verify_precommit(manifest, run_dir)
    pc = dict(precommit) if precommit is not None else None
    if pc is None:
        pc_path = run_dir / PRECOMMIT_NAME
        if pc_path.is_file():
            try:
                pc = load_precommit(pc_path)
            except (ValueError, OSError):
                pc = None
    pre_specified = _pre_specified_status(manifest, precommit_problems)
    proof_present = (run_dir / PRECOMMIT_PROOF_NAME).is_file()
    proof_failed = any(p.startswith("precommit proof:") for p in precommit_problems)
    if precommit is not None:
        # Override supplied: let verify_beacon_binding check hash and proof independently
        _proof_ok: Optional[bool] = None
        _precommit_bound: Optional[bool] = None
    else:
        _proof_ok = True if proof_present and not proof_failed else False if proof_failed else None
        _precommit_bound = True if pre_specified == "precommit bound" else None
    beacon_check: BeaconCheck = verify_beacon_binding(
        manifest,
        run_dir,
        client=beacon_client,
        precommit=pc,
        proof_ok=_proof_ok,
        precommit_bound=_precommit_bound,
    )
    notes: List[str] = []
    from farm_notary.diagnose import diagnostics_from_receipt, format_diagnostics
    from farm_notary.reproduce import load_receipt

    if (run_dir / RECEIPT_NAME).is_file():
        try:
            notes = format_diagnostics(diagnostics_from_receipt(load_receipt(run_dir)))
        except (ValueError, OSError, TypeError):
            notes = []
    notes.extend(beacon_check.notes)
    tamper_evident = "pass" if not tamper_problems else "fail"
    existed_by = _existed_by_status(manifest, run_dir, anchor_problems)
    bitwise_reproducible = _bitwise_status(manifest, run_dir, receipt_problems)

    # Resolve whether the receipt has a verified Sigstore signature so
    # evaluate_ladder can award L3.  verify_receipt already ran cosign when
    # available, so we read its result from receipt_problems rather than
    # calling verify_sigstore_bundle a second time.
    receipt_sigstore_verified = False
    if (run_dir / RECEIPT_NAME).is_file():
        try:
            _receipt = load_receipt(run_dir)
            _bundle = _receipt.get("sigstore")
            if _bundle:
                from farm_notary.sigstore import cosign_available, extract_bundle_identity

                _sigstore_probs = [p for p in receipt_problems if p.startswith("sigstore:")]
                if cosign_available() and not _sigstore_probs:
                    receipt_sigstore_verified = True
                    _identity = extract_bundle_identity(_bundle)
                    if _identity.get("subject"):
                        notes.append(f"sigstore identity: {_identity['subject']}")
                    if _identity.get("issuer"):
                        notes.append(f"sigstore issuer: {_identity['issuer']}")
                elif not cosign_available():
                    notes.append(
                        "sigstore bundle present but cosign not on PATH; "
                        "install cosign to verify"
                    )
        except (ValueError, OSError, TypeError):
            pass

    ladder = evaluate_ladder(
        SimpleNamespace(tamper_evident=tamper_evident, existed_by=existed_by),
        manifest,
        beacon_gaps=beacon_check.gaps,
        receipt_sigstore=receipt_sigstore_verified,
    )
    return ClaimCard(
        tamper_evident=tamper_evident,
        existed_by=existed_by,
        pre_specified=pre_specified,
        bitwise_reproducible=bitwise_reproducible,
        problems=tamper_problems
        + anchor_problems
        + receipt_problems
        + precommit_problems
        + beacon_check.problems,
        notes=notes,
        ladder=ladder,
    )
