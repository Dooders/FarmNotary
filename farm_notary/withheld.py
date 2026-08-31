"""Salted Merkle commitment over unpublished files.

Publication scope, not a privacy protocol. The official record is the
allowlist. Everything else among the candidate files is *withheld*: the
manifest records how many files fell in each exclusion class and a
commitment to their path+bytes. Names are never printed.

Low-entropy ballots must not be hashed in the clear (enumeration). Each
run uses a fresh salt; the leaf is
``SHA-256(salt || 0x00 || path_utf8 || 0x00 || content)``.
Parents are ``SHA-256(0x01 || left || right)``; an odd leftover leaf is
promoted unchanged. The salt is public on the manifest so a later reveal
can be checked without a secret. That still blocks a global rainbow
table of unsalted ballot hashes.

A subset can be revealed later without changing the root: the operator
names the paths, we emit Merkle inclusion proofs, and the rest stay
opaque.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from farm_notary.schema import PRIVATE_NAME_FRAGMENTS

CLASS_DENYLIST = "denylist"
CLASS_UNMATCHED = "unmatched"

CLASS_REASONS: Dict[str, str] = {
    CLASS_DENYLIST: (
        "path matches a withheld-name fragment "
        "(ballot, vote, voter, individual_choice, private)"
    ),
    CLASS_UNMATCHED: "no publish pattern matched",
}

SALT_BYTES = 32


@dataclass(frozen=True)
class WithheldFile:
    """One unpublished candidate. The path is for computation, not printing."""

    rel_path: str
    cls: str
    path: Path


@dataclass
class WithheldCommitment:
    salt: str
    root: str
    classes: Dict[str, Dict[str, object]]

    def to_fields(self) -> Dict[str, object]:
        return {
            "withheld_salt": self.salt,
            "withheld_root": self.root,
            "withheld_classes": self.classes,
        }


@dataclass
class RevealEntry:
    path: str
    cls: str
    leaf: str
    proof: List[Dict[str, str]]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "class": self.cls,
            "leaf": self.leaf,
            "proof": self.proof,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "RevealEntry":
        raw_proof = data.get("proof") or []
        if not isinstance(raw_proof, list):
            raise ValueError("reveal proof must be a list")
        proof: List[Dict[str, str]] = []
        for item in raw_proof:
            if not isinstance(item, dict):
                raise ValueError("reveal proof entry must be an object")
            side = item.get("side")
            digest = item.get("hash")
            if side not in ("left", "right") or not isinstance(digest, str):
                raise ValueError("reveal proof entry needs side and hash")
            proof.append({"side": side, "hash": digest})
        return cls(
            path=str(data["path"]),
            cls=str(data.get("class") or data.get("cls") or ""),
            leaf=str(data["leaf"]),
            proof=proof,
        )


def generate_salt() -> str:
    return secrets.token_hex(SALT_BYTES)


def _parse_salt(salt_hex: str) -> bytes:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise ValueError("withheld_salt must be hex") from exc
    if len(salt) != SALT_BYTES:
        raise ValueError(
            f"withheld_salt must be {SALT_BYTES} bytes ({SALT_BYTES * 2} hex chars)"
        )
    return salt


def leaf_digest(salt: bytes, rel_path: str, content: bytes) -> bytes:
    """Salted commitment to one withheld file. Never store unsalted hashes."""
    h = hashlib.sha256()
    h.update(salt)
    h.update(b"\x00")
    h.update(rel_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(content)
    return h.digest()


def _parent(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    if not leaves:
        return hashlib.sha256(b"").digest()
    level = list(leaves)
    while len(level) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 == len(level):
                nxt.append(level[i])
            else:
                nxt.append(_parent(level[i], level[i + 1]))
        level = nxt
    return level[0]


def merkle_proof(leaves: Sequence[bytes], index: int) -> List[Dict[str, str]]:
    if index < 0 or index >= len(leaves):
        raise IndexError(f"leaf index {index} out of range")
    proof: List[Dict[str, str]] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if idx % 2 == 0:
            sib = idx + 1
            if sib < len(level):
                proof.append({"side": "right", "hash": level[sib].hex()})
        else:
            proof.append({"side": "left", "hash": level[idx - 1].hex()})
        nxt: List[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 == len(level):
                nxt.append(level[i])
            else:
                nxt.append(_parent(level[i], level[i + 1]))
        level = nxt
        idx //= 2
    return proof


def verify_merkle_proof(leaf: bytes, proof: Sequence[Mapping[str, str]], root: bytes) -> bool:
    current = leaf
    for step in proof:
        side = step.get("side")
        try:
            sibling = bytes.fromhex(step.get("hash") or "")
        except ValueError:
            return False
        if side == "left":
            current = _parent(sibling, current)
        elif side == "right":
            current = _parent(current, sibling)
        else:
            return False
    return current == root


def classify_withheld(
    run_dir: Path,
    publish_patterns: Sequence[str],
    *,
    is_private,
    matches_pattern,
    candidates: Iterable[Path],
) -> List[WithheldFile]:
    """Classify unpublished candidates. Does not print names."""
    withheld: List[WithheldFile] = []
    for path in candidates:
        rel = path.relative_to(run_dir).as_posix()
        if is_private(rel):
            withheld.append(WithheldFile(rel, CLASS_DENYLIST, path))
        elif not matches_pattern(rel, publish_patterns):
            withheld.append(WithheldFile(rel, CLASS_UNMATCHED, path))
    withheld.sort(key=lambda item: item.rel_path)
    return withheld


def commit_withheld(
    withheld: Sequence[WithheldFile],
    *,
    salt_hex: Optional[str] = None,
) -> Optional[WithheldCommitment]:
    """Return the commitment, or None when nothing was withheld."""
    if not withheld:
        return None
    salt_hex = salt_hex or generate_salt()
    salt = _parse_salt(salt_hex)
    leaves: List[bytes] = []
    counts: Dict[str, int] = {CLASS_DENYLIST: 0, CLASS_UNMATCHED: 0}
    for item in sorted(withheld, key=lambda x: x.rel_path):
        content = item.path.read_bytes()
        leaves.append(leaf_digest(salt, item.rel_path, content))
        counts[item.cls] = counts.get(item.cls, 0) + 1
    classes: Dict[str, Dict[str, object]] = {}
    for cls, count in counts.items():
        if count:
            classes[cls] = {"count": count, "reason": CLASS_REASONS[cls]}
    return WithheldCommitment(
        salt=salt_hex,
        root=merkle_root(leaves).hex(),
        classes=classes,
    )


def reveal_withheld(
    withheld: Sequence[WithheldFile],
    paths: Sequence[str],
    *,
    salt_hex: str,
) -> List[RevealEntry]:
    """Build inclusion proofs for the named paths. Other names stay off the record."""
    if not paths:
        raise ValueError("reveal requires at least one --path (names are not listed by default)")
    salt = _parse_salt(salt_hex)
    ordered = sorted(withheld, key=lambda item: item.rel_path)
    by_path = {item.rel_path: (i, item) for i, item in enumerate(ordered)}
    leaves = [
        leaf_digest(salt, item.rel_path, item.path.read_bytes()) for item in ordered
    ]
    wanted = [p.replace("\\", "/") for p in paths]
    missing = [p for p in wanted if p not in by_path]
    if missing:
        # Do not echo the missing names — the operator already typed them.
        raise ValueError(
            f"{len(missing)} requested path(s) are not in the withheld set"
        )
    entries: List[RevealEntry] = []
    for rel in wanted:
        index, item = by_path[rel]
        entries.append(
            RevealEntry(
                path=rel,
                cls=item.cls,
                leaf=leaves[index].hex(),
                proof=merkle_proof(leaves, index),
            )
        )
    return entries


def verify_reveal(
    entries: Sequence[RevealEntry],
    *,
    salt_hex: str,
    root_hex: str,
    run_dir: Optional[Path] = None,
) -> List[str]:
    """Check that each reveal entry is in the committed tree.

    When *run_dir* is given, re-read each file and confirm the leaf matches
    the salted path+bytes. Unsalted content hashes are never compared or stored.
    """
    problems: List[str] = []
    try:
        salt = _parse_salt(salt_hex)
        root = bytes.fromhex(root_hex)
    except ValueError as exc:
        return [f"withheld commitment is malformed: {exc}"]
    if len(root) != 32:
        return ["withheld_root must be 32 bytes"]
    for entry in entries:
        leaf = bytes.fromhex(entry.leaf) if _is_hex(entry.leaf) else b""
        if len(leaf) != 32:
            problems.append("reveal leaf for a withheld file is not 32 bytes")
            continue
        if run_dir is not None:
            run_dir_resolved = Path(run_dir).resolve()
            file_path = (run_dir_resolved / entry.path).resolve()
            try:
                file_path.relative_to(run_dir_resolved)
            except ValueError:
                problems.append("revealed file path escapes the run directory")
                continue
            if not file_path.is_file():
                problems.append("revealed file is missing from the run directory")
                continue
            expected = leaf_digest(salt, entry.path, file_path.read_bytes())
            if expected != leaf:
                problems.append(
                    "revealed leaf does not match salted path+bytes "
                    "(unsalted hashes are not used)"
                )
                continue
        if not verify_merkle_proof(leaf, entry.proof, root):
            problems.append("reveal proof does not commit to withheld_root")
    return problems


def _is_hex(value: str) -> bool:
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def write_reveal(entries: Sequence[RevealEntry], dest: Path) -> Path:
    dest = Path(dest)
    payload = {
        "schema": "farmnotary.withheld-reveal.v1",
        "entries": [e.to_dict() for e in entries],
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


def load_reveal(path: Path) -> List[RevealEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("entries") or []
    else:
        raise ValueError("reveal file must be a JSON object or list")
    return [RevealEntry.from_dict(item) for item in items]


def class_counts_total(classes: Optional[Mapping[str, Mapping[str, object]]]) -> int:
    if not classes:
        return 0
    total = 0
    for spec in classes.values():
        if not isinstance(spec, Mapping):
            raise ValueError(
                "each withheld_classes entry must be an object with 'count' and 'reason'"
            )
        try:
            count_val = spec.get("count")
            if count_val is None:
                raise ValueError("missing 'count'")
            total += int(count_val)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"withheld_classes entry has invalid 'count': {exc}"
            ) from exc
    return total
