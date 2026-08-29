"""Stacked reader ladder (L0–L3) printed by ``farm-notary verify``.

The claim card rows stay orthogonal. This module collapses those rows (plus
two reserved gaps) into the highest *earned* level and the next missing
check. ``claim_level`` in ``farm_notary.claims`` is a different vocabulary
(paper-pack / index artifact labels). Do not mix them.

L2 (beacon-derived seed) and L3 (independent identity) are unearn-able
until those features exist. A self-run ``reproduction.json`` never yields L3.
L1 means the re-execution inputs are on the record, not that ``verify`` ran
the command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

LADDER_LEVELS: Tuple[str, ...] = ("L0", "L1", "L2", "L3")

LADDER_NONE = "none"

L0_MEANING = "these bytes existed by time T; Bitcoin headers not verified by this tool"
L1_MEANING = "re-execution specified (command was not run)"

LADDER_MEANINGS = {
    LADDER_NONE: "no earned ladder level",
    "L0": L0_MEANING,
    "L1": L1_MEANING,
    "L2": "seed not grindable after the plan",
    "L3": "independent identity reproduced it",
}

L2_NEXT_MEANING = "seed not beacon-derived (not implemented)"
L3_NEXT_MEANING = "independent identity has not signed a receipt (not implemented)"

BITCOIN_HEIGHT_PREFIX = "Bitcoin height"
FINGERPRINT_KEYS: Tuple[str, ...] = ("os", "arch", "python")


@dataclass(frozen=True)
class LadderResult:
    """Highest earned ladder level and the gap that blocks the next one."""

    level: str
    meaning: str
    next_level: str
    next_meaning: str
    next_gaps: List[str] = field(default_factory=list)

    def lines(self) -> List[str]:
        header = [f"level: {self.level} — {self.meaning}"]
        nxt = f"next:  {self.next_level} — {self.next_meaning}"
        if self.next_gaps:
            nxt += f" ({', '.join(self.next_gaps)})"
        header.append(nxt)
        return header


def _has_bitcoin_attestation(existed_by: str) -> bool:
    return existed_by.startswith(BITCOIN_HEIGHT_PREFIX)


def _nonempty(value: Any) -> bool:
    return bool(value and str(value).strip())


def _command_recorded(manifest: Any) -> bool:
    return _nonempty(getattr(manifest, "command", None))


def _git_sha_recorded(manifest: Any) -> bool:
    return _nonempty(getattr(manifest, "git_sha", None))


def _environment_recorded(manifest: Any) -> bool:
    env = getattr(manifest, "environment", None) or {}
    if not isinstance(env, dict):
        return False
    return all(_nonempty(env.get(key)) for key in FINGERPRINT_KEYS)


def _result(
    level: str, next_level: str, next_gaps: Optional[Sequence[str]] = None
) -> LadderResult:
    if next_level == "L2":
        next_meaning = L2_NEXT_MEANING
    elif next_level == "L3":
        next_meaning = L3_NEXT_MEANING
    else:
        next_meaning = LADDER_MEANINGS[next_level]
    return LadderResult(
        level=level,
        meaning=LADDER_MEANINGS[level],
        next_level=next_level,
        next_meaning=next_meaning,
        next_gaps=list(next_gaps or ()),
    )


def evaluate_ladder(card: Any, manifest: Any) -> LadderResult:
    """Return the stacked level earned by ``card`` against ``manifest``.

    L0 requires a passing tamper-evident row *and* a Bitcoin-height time
    claim. Pending OTS (any calendar), dry-run, EAS, and failed proofs do
    not earn L0. FarmNotary does not check Bitcoin headers; L0 is a
    commitment-plus-attestation-type check. L1 requires a recorded command,
    git SHA, and environment fingerprint; it does not mean the command ran.
    L2 and L3 are reserved and never returned as ``level``.
    """
    if getattr(card, "tamper_evident", None) != "pass":
        return _result(LADDER_NONE, "L0", ["tamper-evident failed"])
    if not _has_bitcoin_attestation(getattr(card, "existed_by", "") or ""):
        return _result(LADDER_NONE, "L0", ["missing: Bitcoin attestation"])

    l1_gaps: List[str] = []
    if not _command_recorded(manifest):
        l1_gaps.append("missing: command")
    if not _git_sha_recorded(manifest):
        l1_gaps.append("missing: git SHA")
    if not _environment_recorded(manifest):
        l1_gaps.append("missing: environment fingerprint")
    if l1_gaps:
        return _result("L0", "L1", l1_gaps)
    return _result("L1", "L2")
