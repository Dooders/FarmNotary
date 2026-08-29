"""Static public index of published AgentFarm manifests.

A directory, not a chain and not a scoreboard: experiment name, seed, CID,
claim level, date.  Reviewers get a page instead of Discord links.  Scores
and rankings are out of scope and are never written.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from farm_notary.campaign import Campaign, extract_seed
from farm_notary.claims import infer_claim_level
from farm_notary.manifest import Manifest
from farm_notary.schema import REGISTRY_VERSION

ENTRIES_NAME = "registry.json"
INDEX_NAME = "index.md"

# Columns a reviewer asked for.  Anything that looks like a score is dropped.
PUBLIC_COLUMNS = ("experiment", "seed", "cid", "claim_level", "date")
FORBIDDEN_KEYS = frozenset(
    {
        "score",
        "scores",
        "rank",
        "ranking",
        "reputation",
        "leaderboard",
        "rating",
    }
)


class RegistryError(RuntimeError):
    pass


def registry_paths(registry: Path) -> tuple[Path, Path]:
    """Return ``(markdown_path, json_path)`` for a registry location."""
    registry = Path(registry)
    if registry.suffix in {".md", ".markdown"}:
        return registry, registry.with_suffix(".json")
    if registry.suffix == ".json":
        return registry.with_suffix(".md"), registry
    registry.mkdir(parents=True, exist_ok=True)
    return registry / INDEX_NAME, registry / ENTRIES_NAME


def load_entries(json_path: Path) -> List[dict]:
    if not json_path.is_file():
        return []
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("entries") or []
    else:
        raise RegistryError(f"{json_path}: expected a JSON object or list")
    cleaned = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        cleaned.append(_public_entry(item))
    return cleaned


def _public_entry(item: Mapping[str, Any]) -> dict:
    for key in FORBIDDEN_KEYS:
        if key in item:
            raise RegistryError(
                f"registry entry contains forbidden key {key!r}; "
                "the public index is not a scoreboard"
            )
    entry = {col: item.get(col) for col in PUBLIC_COLUMNS}
    # content_hash is kept in the JSON sidecar for upsert identity only —
    # it is not a score and is not rendered in the markdown table.
    if item.get("content_hash"):
        entry["content_hash"] = item["content_hash"]
    return entry


def _escape_md_cell(value: Any) -> str:
    """Escape a value for safe insertion into a Markdown table cell."""
    text = "—" if value is None or value == "" else str(value)
    # Replace pipe characters and newlines that would break the table structure.
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("\n", " ").replace("\r", " ")
    # Escape backticks to avoid inline-code injection.
    text = text.replace("`", "\\`")
    # Strip a leading < that could start an HTML tag.
    if text.startswith("<"):
        text = "\\<" + text[1:]
    return text


def render_index(entries: Sequence[Mapping[str, Any]]) -> str:
    """Markdown directory.  No scores, ranks, or reputation columns."""
    lines = [
        "# Published AgentFarm manifests",
        "",
        "A static directory of notarized runs. This is **not** a leaderboard, ",
        "score, or ranking — only experiment name, seed, CID, claim level, and date.",
        "Anchoring is outsourced (OpenTimestamps / IPFS); this page is just an index.",
        "",
        "| Experiment | Seed | CID | Claim | Date |",
        "|---|---|---|---|---|",
    ]
    ordered = sorted(
        entries,
        key=lambda e: (
            str(e.get("date") or ""),
            str(e.get("experiment") or ""),
            _seed_sort_key(e.get("seed")),
        ),
    )
    if not ordered:
        lines.append("| — | — | — | — | — |")
    for item in ordered:
        cid = item.get("cid") or "—"
        lines.append(
            "| {experiment} | {seed} | `{cid}` | {claim} | {date} |".format(
                experiment=_escape_md_cell(item.get("experiment")),
                seed=_escape_md_cell(item.get("seed")),
                cid=_escape_md_cell(cid),
                claim=_escape_md_cell(item.get("claim_level") or "bytes"),
                date=_escape_md_cell(item.get("date")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _display(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _seed_sort_key(seed: Any):
    try:
        return (0, int(seed))
    except (TypeError, ValueError):
        return (1, str(seed) if seed is not None else "")


def write_registry(entries: Sequence[Mapping[str, Any]], registry: Path) -> tuple[Path, Path]:
    md_path, json_path = registry_paths(registry)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    public = [_public_entry(e) for e in entries]
    payload = {
        "schema": REGISTRY_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": public,
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_index(public), encoding="utf-8")
    return md_path, json_path


def entry_from_manifest(
    manifest: Manifest,
    run_dir: Optional[Path] = None,
    *,
    experiment: Optional[str] = None,
    claim_level: Optional[str] = None,
    date: Optional[str] = None,
) -> dict:
    name = experiment or manifest.runner or (manifest.config or {}).get("experiment") or "experiment"
    seed = extract_seed(manifest.config)
    return {
        "experiment": name,
        "seed": seed,
        "cid": manifest.cid,
        "claim_level": claim_level or infer_claim_level(manifest, run_dir),
        "date": (date or (manifest.created_utc or "")[:10]) or None,
        "content_hash": manifest.content_hash(),
    }


def entries_from_campaign(
    campaign: Campaign,
    *,
    experiment: Optional[str] = None,
    date: Optional[str] = None,
) -> List[dict]:
    name = experiment or campaign.name or "campaign"
    default_date = date or (campaign.created_utc or "")[:10] or None
    rows = []
    for run in campaign.runs:
        if not isinstance(run, dict):
            continue
        rows.append(
            {
                "experiment": name,
                "seed": run.get("seed"),
                "cid": run.get("cid"),
                "claim_level": run.get("claim_level") or infer_claim_level(campaign),
                "date": default_date,
                "content_hash": run.get("content_hash"),
            }
        )
    return rows


def upsert_entries(existing: List[dict], incoming: Sequence[Mapping[str, Any]]) -> List[dict]:
    """Replace an entry with the same content_hash, or CID+experiment+seed."""
    merged = list(existing)
    for item in incoming:
        public = _public_entry(item)
        idx = _find_existing(merged, public)
        if idx is None:
            merged.append(public)
        else:
            merged[idx] = public
    return merged


def _find_existing(entries: List[dict], item: Mapping[str, Any]) -> Optional[int]:
    digest = item.get("content_hash")
    if digest:
        for i, old in enumerate(entries):
            if old.get("content_hash") == digest:
                return i
    key = (item.get("experiment"), item.get("seed"), item.get("cid"))
    if key[2]:
        for i, old in enumerate(entries):
            if (old.get("experiment"), old.get("seed"), old.get("cid")) == key:
                return i
    return None


def add_to_registry(
    registry: Path,
    incoming: Sequence[Mapping[str, Any]],
) -> tuple[Path, Path, int]:
    md_path, json_path = registry_paths(registry)
    existing = load_entries(json_path)
    merged = upsert_entries(existing, incoming)
    write_registry(merged, registry)
    return md_path, json_path, len(incoming)
