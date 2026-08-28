"""Named publish profiles: official artifacts for each experiment type.

Allowlist-first is the privacy model, but an empty default means every lab
invents globs — and some will forget REPORT.md or include a path they should
not. A profile is a checked-in list of official artifacts. The denylist is
applied after the allowlist on every profile.

The resolved ``publish_patterns`` are recorded on the manifest so the policy
is part of the claim, not a local convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from farm_notary.schema import PRIVATE_NAME_FRAGMENTS

PROFILE_NAMES = ("consensus", "rl-sweep", "evolution-run")


@dataclass(frozen=True)
class PublishProfile:
    """A named allowlist of official artifacts, plus the shared denylist."""

    name: str
    description: str
    patterns: Tuple[str, ...]
    denylist: Tuple[str, ...] = PRIVATE_NAME_FRAGMENTS


# Official record only. Individual choices, genomes, replay buffers, and
# checkpoints are not listed — the denylist is a second pass over anything
# a later ``--publish`` glob might try to add.
PUBLISH_PROFILES: Dict[str, PublishProfile] = {
    "consensus": PublishProfile(
        name="consensus",
        description="Official record of a consensus / selection experiment",
        patterns=(
            "trials.csv",
            "summary.csv",
            "allocation_means.csv",
            "contrasts.csv",
            "REPORT.md",
            "run_config.json",
            "*.png",
        ),
    ),
    "rl-sweep": PublishProfile(
        name="rl-sweep",
        description="Official record of an RL hyperparameter sweep",
        patterns=(
            "summary.csv",
            "metrics.csv",
            "sweep.csv",
            "REPORT.md",
            "run_config.json",
            "*.png",
        ),
    ),
    "evolution-run": PublishProfile(
        name="evolution-run",
        description="Official record of an evolution / genetic experiment",
        patterns=(
            "summary.csv",
            "fitness.csv",
            "generations.csv",
            "REPORT.md",
            "run_config.json",
            "*.png",
        ),
    ),
}


def get_profile(name: str) -> PublishProfile:
    """Return the named profile, or raise ``ValueError`` listing known names."""
    try:
        return PUBLISH_PROFILES[name]
    except KeyError:
        known = ", ".join(PROFILE_NAMES)
        raise ValueError(
            f"unknown publish profile {name!r}; expected one of: {known}"
        ) from None


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def resolve_publish_policy(
    *,
    profile: Optional[str] = None,
    publish_patterns: Optional[Sequence[str]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[str], List[str]]:
    """Resolve (profile_name, allowlist) from profile, config, and extra globs.

    Sources, in order (later entries append; duplicates are dropped):

    1. Named profile (``profile`` argument, else ``notary.profile`` in config)
    2. ``notary.publish`` in the run config
    3. ``publish_patterns`` (e.g. ``--publish`` CLI flags)
    """
    cfg_profile: Optional[str] = None
    cfg_publish: List[str] = []
    if config:
        notary = config.get("notary") or {}
        if isinstance(notary, dict):
            raw_profile = notary.get("profile")
            if isinstance(raw_profile, str) and raw_profile.strip():
                cfg_profile = raw_profile.strip()
            raw_publish = notary.get("publish") or []
            if isinstance(raw_publish, list):
                cfg_publish = [str(p) for p in raw_publish if p]

    name = profile or cfg_profile
    patterns: List[str] = []
    if name:
        patterns.extend(get_profile(name).patterns)
    patterns.extend(cfg_publish)
    if publish_patterns:
        patterns.extend(str(p) for p in publish_patterns if p)
    return name, _unique(patterns)
