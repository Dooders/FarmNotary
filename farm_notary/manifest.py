from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Tuple

from farm_notary.schema import MANIFEST_VERSION, PRIVATE_NAME_FRAGMENTS, REQUIRED_KEYS

MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "reproduction.json"

# Notary metadata written next to the artifacts, never treated as artifacts.
NOTARY_FILE_NAMES = frozenset({MANIFEST_NAME, RECEIPT_NAME, "precommit.json"})
NOTARY_FILE_SUFFIXES = (".ots",)


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_json(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def is_private_path(rel_path: str) -> bool:
    """True if any part of the relative path names private data."""
    lowered = rel_path.lower()
    return any(frag in lowered for frag in PRIVATE_NAME_FRAGMENTS)


def iter_artifact_paths(run_dir: Path) -> Iterator[Path]:
    """All hashable files under run_dir, recursively.

    Skips notary metadata (manifest.json, reproduction.json, *.ots proofs),
    hidden files/directories, and anything whose relative path matches a
    private-name fragment (ballots, votes, ...).
    """
    run_dir = Path(run_dir)
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name in NOTARY_FILE_NAMES or path.name.endswith(NOTARY_FILE_SUFFIXES):
            continue
        rel_posix = rel.as_posix()
        if is_private_path(rel_posix):
            continue
        yield path


def _git(args, cwd: Optional[Path] = None) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=cwd, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def detect_git_status(cwd: Optional[Path] = None) -> Tuple[Optional[str], Optional[bool]]:
    """Return (HEAD sha, dirty flag), or (None, None) outside a repo.

    A dirty tree means the sha does not identify the code that actually ran,
    so it is recorded rather than hidden.
    """
    sha_out = _git(["rev-parse", "HEAD"], cwd=cwd)
    sha = sha_out.strip() if sha_out else None
    if not sha:
        return None, None
    status = _git(["status", "--porcelain"], cwd=cwd)
    dirty = bool(status.strip()) if status is not None else None
    return sha, dirty


def detect_git_sha(cwd: Optional[Path] = None) -> Optional[str]:
    return detect_git_status(cwd)[0]


def capture_environment(lockfile: Optional[Path] = None) -> dict:
    """Snapshot the execution environment for the provenance record.

    packages_hash is the SHA-256 of the sorted `name==version` list of every
    installed distribution: two environments with the same hash have the same
    package set. A lockfile hash is recorded when one is provided.
    """
    from importlib import metadata

    dists = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            dists.add(f"{name}=={dist.version}")
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages_hash": hashlib.sha256(
            "\n".join(sorted(dists)).encode("utf-8")
        ).hexdigest(),
        "package_count": len(dists),
    }
    if lockfile is not None:
        env["lockfile"] = Path(lockfile).name
        env["lockfile_sha256"] = hash_file(Path(lockfile))
    return env


@dataclass
class Manifest:
    schema: str = MANIFEST_VERSION
    created_utc: str = ""
    git_sha: Optional[str] = None
    git_dirty: Optional[bool] = None
    runner: Optional[str] = None
    # Exact invocation that produced the run; "{run_dir}" marks the output
    # directory so `farm-notary reproduce` can re-run into a fresh one.
    command: Optional[str] = None
    environment: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    artifact_hashes: dict = field(default_factory=dict)
    official_record: dict = field(default_factory=dict)
    precommit_hash: Optional[str] = None
    cid: Optional[str] = None
    anchor: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Manifest":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def content_hash(self) -> str:
        """Hash of the manifest body, excluding cid and anchor receipt.

        Excluding those fields lets the manifest be stamped with upload and
        anchor results after the fact without changing what was anchored.
        """
        body = self.to_dict()
        body.pop("cid", None)
        body.pop("anchor", None)
        return hash_json(body)

    def validate(self) -> None:
        data = self.to_dict()
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"manifest missing keys: {missing}")
        if data["schema"] != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest schema {data['schema']!r}, expected {MANIFEST_VERSION!r}"
            )
        listed = set(self.artifacts)
        hashed = set(self.artifact_hashes)
        if listed != hashed:
            raise ValueError(
                f"artifacts and artifact_hashes disagree: "
                f"only listed {sorted(listed - hashed)}, only hashed {sorted(hashed - listed)}"
            )
        private = sorted(name for name in listed if is_private_path(name))
        if private:
            raise ValueError(f"manifest contains private artifacts: {private}")


def build_manifest(
    run_dir: Path,
    *,
    config: Optional[Mapping[str, Any]] = None,
    git_sha: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    runner: Optional[str] = None,
    command: Optional[str] = None,
    environment: Optional[Mapping[str, Any]] = None,
    lockfile: Optional[Path] = None,
    official_record: Optional[Mapping[str, Any]] = None,
    precommit_path: Optional[Path] = None,
) -> Manifest:
    run_dir = Path(run_dir)
    artifacts: list = []
    hashes: dict = {}
    for path in iter_artifact_paths(run_dir):
        rel = path.relative_to(run_dir).as_posix()
        artifacts.append(rel)
        hashes[rel] = hash_file(path)
    if git_sha is None:
        git_sha, detected_dirty = detect_git_status()
        if git_dirty is None:
            git_dirty = detected_dirty
    manifest = Manifest(
        created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_sha=git_sha,
        git_dirty=git_dirty,
        runner=runner,
        command=command,
        environment=dict(environment) if environment is not None else capture_environment(lockfile),
        config=dict(config or {}),
        artifacts=artifacts,
        artifact_hashes=hashes,
        official_record=dict(official_record or {}),
    )
    if precommit_path is not None:
        from farm_notary.precommit import load_precommit
        from farm_notary.precommit import precommit_hash as _pc_hash

        pc = load_precommit(Path(precommit_path))
        manifest.precommit_hash = _pc_hash(pc)
    manifest.validate()
    return manifest


def write_manifest(manifest: Manifest, run_dir: Path) -> Path:
    dest = Path(run_dir) / MANIFEST_NAME
    dest.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest


def load_manifest(path: Path) -> Manifest:
    """Load manifest.json from a file path or a run directory."""
    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = Manifest.from_dict(data)
    manifest.validate()
    return manifest
