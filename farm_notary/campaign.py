"""Campaign / sweep parent manifests.

AgentFarm work is rarely one directory.  A parent record lists child run
CIDs, seeds, and a shared config hash so a reviewer can check a paper
figure (100 trials, seed 0…N) instead of a single folder.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from farm_notary.beacon import subset_note
from farm_notary.manifest import (
    MANIFEST_NAME,
    SEED_KEYS,
    Manifest,
    capture_environment,
    config_hash_excluding_seed,
    extract_seed,
    hash_json,
    load_manifest,
)
from farm_notary.precommit import PRECOMMIT_NAME, load_precommit
from farm_notary.precommit import precommit_hash as hash_precommit
from farm_notary.schema import CAMPAIGN_VERSION, TOOL_VERSION

CAMPAIGN_NAME = "campaign.json"


@dataclass
class Campaign:
    schema: str = CAMPAIGN_VERSION
    farm_notary_version: Optional[str] = None
    created_utc: str = ""
    name: Optional[str] = None
    git_sha: Optional[str] = None
    git_dirty: Optional[bool] = None
    command: Optional[str] = None
    environment: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    config_hash: Optional[str] = None
    runs: list = field(default_factory=list)
    publish_patterns: list = field(default_factory=list)
    unmatched_count: int = 0
    precommit_hash: Optional[str] = None
    seed_plan: Optional[dict] = None
    cid: Optional[str] = None
    cid_reachable: Optional[bool] = None
    cid_reachable_checked_utc: Optional[str] = None
    anchor: Optional[dict] = None
    identity: Optional[dict] = None
    pin_service: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        omit_if_none = {
            "farm_notary_version",
            "name",
            "command",
            "config_hash",
            "precommit_hash",
            "seed_plan",
            "cid",
            "cid_reachable",
            "cid_reachable_checked_utc",
            "anchor",
            "identity",
            "pin_service",
        }
        return {k: v for k, v in d.items() if not (k in omit_if_none and v is None)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Campaign":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def content_hash(self) -> str:
        body = self.to_dict()
        body.pop("cid", None)
        body.pop("cid_reachable", None)
        body.pop("cid_reachable_checked_utc", None)
        body.pop("anchor", None)
        body.pop("identity", None)
        body.pop("pin_service", None)
        return hash_json(body)

    def validate(self) -> None:
        if self.schema != CAMPAIGN_VERSION:
            raise ValueError(
                f"unsupported campaign schema {self.schema!r}, expected {CAMPAIGN_VERSION!r}"
            )
        if not self.runs:
            raise ValueError("campaign lists no child runs")
        for i, run in enumerate(self.runs):
            if not isinstance(run, Mapping):
                raise ValueError(f"campaign.runs[{i}] must be an object")
            if not run.get("content_hash"):
                raise ValueError(f"campaign.runs[{i}] is missing content_hash")

    def seed_values(self) -> List[Any]:
        return [run.get("seed") for run in self.runs if isinstance(run, Mapping)]


def _child_claim_level(manifest: Manifest, run_dir: Path) -> str:
    from farm_notary.claims import infer_claim_level

    return infer_claim_level(manifest, run_dir)


def child_entry(manifest: Manifest, run_dir: Path, *, campaign_dir: Optional[Path] = None) -> dict:
    config = manifest.config or {}
    entry: dict = {
        "seed": extract_seed(config),
        "content_hash": manifest.content_hash(),
        "config_hash": config_hash_excluding_seed(config),
        "created_utc": manifest.created_utc,
        "claim_level": _child_claim_level(manifest, run_dir),
    }
    if manifest.cid:
        entry["cid"] = manifest.cid
    if manifest.git_sha:
        entry["git_sha"] = manifest.git_sha
    beacon = getattr(manifest, "beacon", None) or {}
    if isinstance(beacon, Mapping) and beacon.get("seed_index") is not None:
        entry["seed_index"] = int(beacon["seed_index"])
    rel = _relative_child_path(run_dir, campaign_dir)
    if rel:
        entry["path"] = rel
    return entry


def _relative_child_path(run_dir: Path, campaign_dir: Optional[Path]) -> Optional[str]:
    if campaign_dir is None:
        return None
    run_resolved = Path(run_dir).resolve()
    camp_resolved = Path(campaign_dir).resolve()
    try:
        rel = run_resolved.relative_to(camp_resolved)
        return rel.as_posix()
    except ValueError:
        pass
    # run_dir is not under campaign_dir (e.g. a sibling); compute a relative
    # path using the common ancestor so the stored path is resolvable.
    try:
        rel = run_resolved.relative_to(camp_resolved.parent)
        return (".." / rel).as_posix()
    except ValueError:
        pass
    return None


def build_campaign(
    run_dirs: Sequence[Path],
    *,
    name: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
    git_sha: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    command: Optional[str] = None,
    environment: Optional[Mapping[str, Any]] = None,
    lockfile: Optional[Path] = None,
    campaign_dir: Optional[Path] = None,
    precommit_hash: Optional[str] = None,
) -> Campaign:
    """Build a parent campaign from child run directories that already have manifests."""
    if not run_dirs:
        raise ValueError("campaign requires at least one --run-dir")

    runs: List[dict] = []
    child_config_hashes = []
    publish_patterns: List[str] = []
    unmatched_total = 0
    for raw in run_dirs:
        child = Path(raw)
        manifest = load_manifest(child)
        entry = child_entry(manifest, child, campaign_dir=campaign_dir)
        runs.append(entry)
        child_config_hashes.append(entry["config_hash"])
        for pat in manifest.publish_patterns:
            if pat not in publish_patterns:
                publish_patterns.append(pat)
        unmatched_total += int(manifest.unmatched_count or 0)
        if git_sha is None and manifest.git_sha:
            git_sha = manifest.git_sha
            git_dirty = manifest.git_dirty
        if command is None and manifest.command:
            command = manifest.command
        if precommit_hash is None and manifest.precommit_hash:
            precommit_hash = manifest.precommit_hash

    unique_hashes = set(child_config_hashes)
    shared_hash = child_config_hashes[0] if len(unique_hashes) == 1 else None
    campaign_config = dict(config) if config is not None else {}
    if campaign_config and shared_hash is not None:
        supplied_hash = config_hash_excluding_seed(campaign_config)
        if supplied_hash != shared_hash:
            raise ValueError(
                f"supplied --config seed-excluded hash {supplied_hash} does not match "
                f"children's shared config hash {shared_hash}"
            )
    if shared_hash is None and not campaign_config:
        # Still record the first child's config (minus seed) so the parent
        # is inspectable; verify will flag the hash disagreement.
        first = load_manifest(Path(run_dirs[0]))
        campaign_config = {
            k: v for k, v in (first.config or {}).items() if k not in SEED_KEYS
        }

    if environment is None:
        environment = capture_environment(lockfile)

    seed_plan = _shared_seed_plan(run_dirs, precommit_hash)

    campaign = Campaign(
        farm_notary_version=TOOL_VERSION,
        created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        name=name,
        git_sha=git_sha,
        git_dirty=git_dirty,
        command=command,
        environment=dict(environment),
        config=campaign_config,
        config_hash=shared_hash or (config_hash_excluding_seed(campaign_config) if campaign_config else None),
        runs=runs,
        publish_patterns=publish_patterns,
        unmatched_count=unmatched_total,
        precommit_hash=precommit_hash,
        seed_plan=seed_plan,
    )
    campaign.validate()
    return campaign


def write_campaign(campaign: Campaign, dest: Path) -> Path:
    dest = Path(dest)
    if dest.is_dir() or dest.suffix == "":
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / CAMPAIGN_NAME
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(campaign.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest


def load_campaign(path: Path, *, validate: bool = True) -> Campaign:
    path = Path(path)
    if path.is_dir():
        path = path / CAMPAIGN_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    campaign = Campaign.from_dict(data)
    if validate:
        campaign.validate()
    return campaign


def verify_campaign(
    campaign: Campaign,
    campaign_dir: Path,
    *,
    require_local: bool = False,
    checked: Optional[List[int]] = None,
) -> List[str]:
    """Check child hashes, shared config hash, and optional local run dirs."""
    problems: List[str] = []
    try:
        campaign.validate()
    except ValueError as exc:
        problems.append(f"invalid campaign: {exc}")
        return problems

    campaign_dir = Path(campaign_dir)
    parent_hash = campaign.config_hash

    # Cross-check campaign.seed_plan against the precommit it claims to commit.
    canonical_plan: Optional[dict] = None
    if campaign.precommit_hash and isinstance(campaign.seed_plan, Mapping):
        run_dirs = [
            Path(r["path"])
            for r in campaign.runs
            if isinstance(r, Mapping) and r.get("path")
        ]
        canonical_plan = _shared_seed_plan(
            [campaign_dir / p for p in run_dirs], campaign.precommit_hash
        )
        if canonical_plan is not None:
            for field in ("chain_hash", "min_round", "derivation", "inclusion", "count"):
                campaign_value = campaign.seed_plan.get(field)
                committed_value = canonical_plan.get(field)
                if campaign_value != committed_value:
                    problems.append(
                        f"campaign seed_plan.{field} {campaign_value!r} does not match "
                        f"precommit seed_plan.{field} {committed_value!r}"
                    )

    for i, run in enumerate(campaign.runs):
        child_hash = run.get("config_hash")
        if parent_hash and child_hash and child_hash != parent_hash:
            problems.append(
                f"runs[{i}] config_hash {child_hash} does not match campaign config_hash {parent_hash}"
            )
        local = _resolve_child_dir(run, campaign_dir)
        if local is None:
            if require_local:
                problems.append(
                    f"runs[{i}] local run not present"
                    + (f" (cid {run['cid']})" if run.get("cid") else "")
                )
            continue
        try:
            manifest = load_manifest(local)
        except (ValueError, OSError) as exc:
            problems.append(f"runs[{i}] could not load child manifest: {exc}")
            continue
        if checked is not None:
            checked.append(i)
        actual = manifest.content_hash()
        expected = run.get("content_hash")
        if actual != expected:
            problems.append(
                f"runs[{i}] content_hash mismatch: campaign records {expected}, child is {actual}"
            )
        child_cfg = config_hash_excluding_seed(manifest.config)
        if parent_hash and child_cfg != parent_hash:
            problems.append(
                f"runs[{i}] seed-excluded config hash {child_cfg} does not match campaign {parent_hash}"
            )
        if run.get("cid") and manifest.cid and run["cid"] != manifest.cid:
            problems.append(
                f"runs[{i}] cid mismatch: campaign records {run['cid']}, child is {manifest.cid}"
            )

        # Verify that the campaign-recorded seed_index matches the child manifest's beacon record.
        campaign_seed_index = run.get("seed_index")
        if campaign_seed_index is not None:
            child_beacon = getattr(manifest, "beacon", None) or {}
            child_seed_index = child_beacon.get("seed_index") if isinstance(child_beacon, Mapping) else None
            if child_seed_index is not None:
                try:
                    if int(campaign_seed_index) != int(child_seed_index):
                        problems.append(
                            f"runs[{i}] seed_index {campaign_seed_index!r} does not match "
                            f"child manifest beacon seed_index {child_seed_index!r}"
                        )
                except (TypeError, ValueError):
                    problems.append(
                        f"runs[{i}] seed_index {campaign_seed_index!r} is not a valid integer"
                    )

        # Rehash every artifact in the child run directory so that tampering
        # with artifacts (without rewriting the child manifest) is caught.
        from farm_notary.verify import verify_run_dir

        artifact_problems = verify_run_dir(manifest, local)
        for p in artifact_problems:
            problems.append(f"runs[{i}] {p}")
    return problems


def _shared_seed_plan(
    run_dirs: Sequence[Path], precommit_hash: Optional[str]
) -> Optional[dict]:
    """Copy seed_plan from a child precommit when children share that hash."""
    if not precommit_hash:
        return None

    for raw in run_dirs:
        pc_path = Path(raw) / PRECOMMIT_NAME
        if not pc_path.is_file():
            continue
        try:
            pc = load_precommit(pc_path)
        except (ValueError, OSError):
            continue
        if hash_precommit(pc) != precommit_hash:
            continue
        plan = pc.get("seed_plan")
        if isinstance(plan, Mapping):
            return dict(plan)
    return None


def campaign_seed_coverage_note(campaign: Campaign) -> Optional[str]:
    """Reader note: published indices vs the committed seed_plan count."""
    plan = campaign.seed_plan
    if not isinstance(plan, Mapping):
        return None
    try:
        count = int(plan["count"])
    except (KeyError, TypeError, ValueError):
        return None
    published = [
        int(run["seed_index"])
        for run in campaign.runs
        if isinstance(run, Mapping) and run.get("seed_index") is not None
    ]
    if not published:
        return None
    return subset_note(count, published)



def _resolve_child_dir(run: Mapping[str, Any], campaign_dir: Path) -> Optional[Path]:
    rel = run.get("path")
    if not rel:
        return None
    candidate = (campaign_dir / rel).resolve()
    # Guard against traversal attacks: the resolved path must be within the
    # campaign directory's parent (siblings are allowed, but not arbitrary
    # absolute paths or deep escapes).
    try:
        candidate.relative_to(campaign_dir.resolve().parent)
    except ValueError:
        return None
    if (candidate / MANIFEST_NAME).is_file():
        return candidate
    return None
