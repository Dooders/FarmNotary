"""Pre-registration support.

A precommit anchors the *intent* of a run before any artifacts exist.  It
records the config, command, code identity (git SHA), and optional lockfile
hash so that a verifier can confirm the analysis was fully specified before the
result was known — closing the file-drawer loophole that a post-hoc timestamp
cannot address.

Typical flow
------------
1. ``farm-notary precommit --config run_config.json --command "..." --git-sha <sha>``
   writes ``precommit.json`` (+ ``precommit.ots``) before the run.
2. After the run, ``farm-notary manifest`` (or ``notarize_run``) receives
   ``--precommit precommit.json``; the manifest gains a ``precommit_hash``
   field that binds the two phases together.
3. ``farm-notary verify`` reports **pre-specified design** as
   ``precommit bound`` (or ``fail`` / ``missing``) and flags any
   config/command divergence.

A dirty working tree is refused unless ``--allow-dirty``: the SHA would not
identify the code, so the precommit would not be a code-identity claim.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from farm_notary.beacon import BeaconClient, build_seed_plan, config_has_seed
from farm_notary.manifest import (
    hash_file,
    hash_json,
    require_clean_identity,
    resolve_git_identity,
)

PRECOMMIT_NAME = "precommit.json"
PRECOMMIT_PROOF_NAME = "precommit.ots"
PRECOMMIT_VERSION = "farmnotary.precommit.v1"

# Fields in the precommit that must match the final manifest verbatim.
BOUND_FIELDS = ("config", "command", "git_sha")


def build_precommit(
    *,
    config: Optional[Mapping[str, Any]] = None,
    command: Optional[str] = None,
    git_sha: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    lockfile: Optional[Path] = None,
    allow_dirty: bool = False,
    seed_count: Optional[int] = None,
    inclusion: Optional[str] = None,
    delay_rounds: int = 1,
    beacon_client: Optional[BeaconClient] = None,
    seed_plan: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Build a pre-run manifest dict (no artifacts).

    Parameters
    ----------
    config:
        The run configuration that will be passed verbatim to ``build_manifest``
        after the run.
    command:
        The exact command (with ``{run_dir}`` placeholder) that will produce the
        run.
    git_sha:
        Code identity.  Auto-detected from the current working directory when
        omitted.
    git_dirty:
        Whether the working tree is dirty.  Auto-detected when omitted, even
        if *git_sha* was supplied — a SHA is not a substitute for the check.
    lockfile:
        Dependency lockfile whose SHA-256 is recorded for environment pinning.
    allow_dirty:
        If false (the default), raise ``DirtyTreeError`` when the working tree
        is dirty so a precommit cannot claim a code identity it does not have.
    seed_count / inclusion / delay_rounds / beacon_client:
        When *seed_count* is set, record a ``seed_plan`` that binds later
        run seeds to a beacon round after this plan. The config must not
        contain a concrete seed.
    seed_plan:
        An already-built plan (tests). Mutually exclusive with building
        one from *seed_count*.
    """
    git_sha, git_dirty = resolve_git_identity(git_sha, git_dirty)
    cfg = dict(config or {})

    pc: dict = {
        "schema": PRECOMMIT_VERSION,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "command": command,
        "config": cfg,
    }
    if lockfile is not None:
        pc["lockfile"] = Path(lockfile).name
        pc["lockfile_sha256"] = hash_file(Path(lockfile))

    if seed_plan is not None and seed_count is not None:
        raise ValueError("pass seed_plan or seed_count, not both")
    if seed_count is not None:
        if config_has_seed(cfg):
            raise ValueError(
                "precommit config must not contain a seed when --seed-count is set; "
                "derive seeds after the plan is stamped"
            )
        if beacon_client is None:
            raise ValueError("beacon_client is required when seed_count is set")
        pc["seed_plan"] = build_seed_plan(
            count=seed_count,
            inclusion=inclusion or "",
            client=beacon_client,
            delay_rounds=delay_rounds,
        )
    elif seed_plan is not None:
        if config_has_seed(cfg):
            raise ValueError(
                "precommit config must not contain a seed when seed_plan is set"
            )
        pc["seed_plan"] = dict(seed_plan)
    require_clean_identity(pc.get("git_dirty"), allow_dirty=allow_dirty)
    return pc


def precommit_hash(pc: dict) -> str:
    """SHA-256 of the canonical JSON representation of a precommit dict."""
    return hash_json(pc)


def write_precommit(pc: dict, dest: Path) -> Path:
    """Serialise *pc* to *dest* (a file path, not a directory)."""
    dest = Path(dest)
    dest.write_text(json.dumps(pc, indent=2) + "\n", encoding="utf-8")
    return dest


def load_precommit(path: Path) -> dict:
    """Load and return a precommit dict from *path*."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if data.get("schema") != PRECOMMIT_VERSION:
        raise ValueError(
            f"{path}: unsupported precommit schema {data.get('schema')!r}, "
            f"expected {PRECOMMIT_VERSION!r}"
        )
    return data


def require_precommit_proof(pc: dict, pc_path: Path) -> None:
    """Refuse derive-seeds until the plan has a proof that commits to it."""
    from farm_notary.ots import verify_proof

    proof_path = Path(pc_path).parent / PRECOMMIT_PROOF_NAME
    if not proof_path.is_file():
        raise ValueError(
            f"{PRECOMMIT_PROOF_NAME} is required before derive-seeds; "
            "stamp the plan with `precommit --backend ots`"
        )
    problems = verify_proof(proof_path.read_bytes(), precommit_hash(pc))
    if problems:
        raise ValueError("; ".join(problems))
