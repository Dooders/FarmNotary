"""Paper pack: one command that writes the appendix snippet.

The artifact this audience puts in a PDF: CID, content hash, Bitcoin
attestation (or pending), publish allowlist, unmatched count, precommit
hash, claim level, ladder, and a scoped reproducibility sentence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from farm_notary.claims import infer_claim_level, scoped_reproducibility_sentence
from farm_notary.fingerprint import environment_scope
from farm_notary.manifest import Manifest
from farm_notary.verify import evaluate_claims

PAPER_PACK_NAME = "appendix.md"


def bitcoin_attestation_label(record: Any, run_dir: Optional[Path] = None) -> str:
    """Human label: Bitcoin block N, pending, or none.

    EAS is reported as experimental and is not a Bitcoin attestation.
    """
    anchor = getattr(record, "anchor", None) or {}
    if not anchor:
        return "none"
    backend = anchor.get("backend")
    if backend == "dry-run":
        return "none"
    if backend == "eas":
        uid = anchor.get("attestation_uid") or "submitted"
        return f"EAS (experimental) {uid}"
    if backend == "opentimestamps":
        proof_name = (anchor.get("detail") or {}).get("proof", "manifest.ots")
        if run_dir is not None:
            proof_path = Path(run_dir) / proof_name
            if proof_path.is_file():
                try:
                    from farm_notary.ots import proof_status

                    status = proof_status(proof_path.read_bytes())
                except Exception:
                    return "pending"
                if status.bitcoin_heights:
                    height = min(status.bitcoin_heights)
                    return f"Bitcoin block {height}"
                if status.pending_calendars:
                    return "pending"
        detail = anchor.get("detail") or {}
        if detail.get("status") == "pending" or detail.get("calendars"):
            return "pending"
        return "pending"
    return "none"


def build_paper_pack(
    record: Any,
    run_dir: Optional[Path] = None,
    *,
    derived_ok: Optional[bool] = None,
    experiment: Optional[str] = None,
) -> str:
    """Return markdown for a reproducibility appendix."""
    run_dir = Path(run_dir) if run_dir is not None else None
    name = (
        experiment
        or getattr(record, "name", None)
        or getattr(record, "runner", None)
        or "experiment"
    )
    cid = getattr(record, "cid", None) or "—"
    content_hash = record.content_hash()
    attestation = bitcoin_attestation_label(record, run_dir)
    patterns = getattr(record, "publish_patterns", None) or []
    allowlist = ", ".join(f"`{p}`" for p in patterns) if patterns else "—"
    unmatched = getattr(record, "unmatched_count", 0)
    precommit = getattr(record, "precommit_hash", None) or "—"
    claim = infer_claim_level(record, run_dir)
    ladder = "—"
    if run_dir is not None and isinstance(record, Manifest):
        result = evaluate_claims(record, run_dir).ladder
        if result is not None:
            ladder = result.level
    env = environment_scope(getattr(record, "environment", None) or {})
    sentence = scoped_reproducibility_sentence(
        record, derived_ok=derived_ok, experiment=name if name != "experiment" else experiment
    )

    lines = [
        f"## Reproducibility appendix — {name}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| CID | `{cid}` |",
        f"| Content hash | `{content_hash}` |",
        f"| Bitcoin attestation | {attestation} |",
        f"| Publish allowlist | {allowlist} |",
        f"| Unmatched files | {unmatched} |",
        f"| Precommit hash | `{precommit}` |",
        f"| Claim level | {claim} |",
        f"| Ladder | {ladder} |",
        f"| Environment | {env} |",
        "",
        sentence,
        "",
    ]

    runs = getattr(record, "runs", None)
    if runs:
        lines.extend(
            [
                "### Child runs",
                "",
                "| Seed | CID | Content hash | Claim |",
                "|---|---|---|---|",
            ]
        )
        for run in runs:
            if not isinstance(run, dict):
                continue
            seed = run.get("seed", "—")
            child_cid = run.get("cid") or "—"
            child_hash = run.get("content_hash") or "—"
            child_claim = run.get("claim_level") or "bytes"
            lines.append(f"| {seed} | `{child_cid}` | `{child_hash}` | {child_claim} |")
        lines.append("")

    identity = getattr(record, "identity", None) or {}
    if identity.get("scheme"):
        principal = identity.get("principal") or "lab key"
        lines.append(
            f"Optional identity: {identity['scheme']} signature by `{principal}` "
            f"over the content hash. Reviewers who know this key can attribute "
            f"the publication; everyone else still has OpenTimestamps."
        )
        lines.append("")

    return "\n".join(lines)


def write_paper_pack(markdown: str, dest: Path) -> Path:
    dest = Path(dest)
    if dest.is_dir() or dest.suffix == "":
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / PAPER_PACK_NAME
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown if markdown.endswith("\n") else markdown + "\n", encoding="utf-8")
    return dest
