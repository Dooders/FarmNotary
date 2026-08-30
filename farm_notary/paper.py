"""Paper pack: one command that writes the appendix snippet.

The artifact this audience puts in a PDF: CID, content hash, Bitcoin
attestation (or pending), publish allowlist, unmatched count, precommit
hash, artifact label, and a scoped reproducibility sentence. The reader
ladder is not cited here (see ``PAPER_LADDER_NOTE``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from farm_notary.claims import infer_claim_level, scoped_reproducibility_sentence
from farm_notary.fingerprint import environment_scope

PAPER_PACK_NAME = "appendix.md"

# Appendix tables do not cite Ln: FarmNotary does not verify Bitcoin headers.
PAPER_LADDER_CELL = "—"
PAPER_LADDER_NOTE = (
    "Reader ladder levels are printed by `farm-notary verify`. This appendix "
    "does not cite `Ln` because FarmNotary does not verify Bitcoin block "
    "headers (`ots verify`). A campaign has no single-run ladder."
)


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
                if status.public_pending_calendars and not status.unknown_pending_calendars:
                    return "Pending (unverified claim; public OpenTimestamps calendars)"
                if status.unknown_pending_calendars and not status.public_pending_calendars:
                    label = (
                        "calendar"
                        if len(status.unknown_pending_calendars) == 1
                        else "calendars"
                    )
                    return (
                        f"Pending at user-supplied {label} "
                        f"{', '.join(status.unknown_pending_calendars)} "
                        "(unverified claim; untrusted until Bitcoin)"
                    )
                if status.pending_calendars:
                    return (
                        "Pending (unverified claims; public calendars; user-supplied calendars "
                        "remain untrusted until Bitcoin)"
                    )
        return "Pending (calendar attestation only)"
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
        f"| Artifact label | {claim} |",
        f"| Reader ladder | {PAPER_LADDER_CELL} |",
        f"| Environment | {env} |",
        "",
        PAPER_LADDER_NOTE,
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
