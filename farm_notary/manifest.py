from __future__ import annotations

import fnmatch
import hashlib
import json
import platform
import subprocess
import warnings
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Tuple

from farm_notary.schema import MANIFEST_VERSION, PRIVATE_NAME_FRAGMENTS, REQUIRED_KEYS, TOOL_VERSION

MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "reproduction.json"

# Notary metadata written next to the artifacts, never treated as artifacts.
NOTARY_FILE_NAMES = frozenset(
    {MANIFEST_NAME, RECEIPT_NAME, "precommit.json", "campaign.json", "appendix.md"}
)
NOTARY_FILE_SUFFIXES = (".ots",)

# Seed keys stripped when hashing a sweep's shared config.
SEED_KEYS = ("seed", "rng_seed", "random_seed")

# Stamp fields that may be added after content_hash is computed.
_STAMP_KEYS = (
    "cid",
    "cid_reachable",
    "cid_reachable_checked_utc",
    "anchor",
    "identity",
)


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


def _matches_any_pattern(rel_posix: str, patterns: Sequence[str]) -> bool:
    """True if rel_posix matches at least one glob pattern.

    Pattern semantics mirror :func:`fnmatch.fnmatch`: ``*`` matches any
    sequence of characters **within** a path component; use ``**`` only if
    you need to cross directory boundaries (handled via a double-check on
    the filename alone for simple ``*.ext`` patterns so that
    ``--publish '*.png'`` matches ``subdir/chart.png``).
    """
    filename = Path(rel_posix).name
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        # Allow simple extension/name globs (e.g. "*.png") to match files
        # in subdirectories without requiring "**/*.png".
        if fnmatch.fnmatch(filename, pat):
            return True
    return False


def iter_artifact_paths(
    run_dir: Path,
    publish_patterns: Sequence[str],
) -> Iterator[Path]:
    """Yield hashable files under *run_dir* that match the allowlist.

    Only files whose relative POSIX path (or filename) matches at least one
    *publish_patterns* glob are yielded.  The denylist (PRIVATE_NAME_FRAGMENTS)
    is applied as a second, belt-and-braces pass over whatever the allowlist
    admits.  Hidden files/directories and notary metadata are always skipped.
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
        # --- allowlist (primary gate) ---
        if not _matches_any_pattern(rel_posix, publish_patterns):
            continue
        # --- denylist (belt-and-braces) ---
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

    First-class fingerprint fields (os, arch, python, optional numpy/BLAS)
    sit alongside the package-set hash and optional lockfile hash.  Two
    environments with the same packages_hash can still differ by OS, arch,
    or BLAS; the fingerprint keeps a bitwise claim scoped to the machine
    class that earned it.
    """
    from importlib import metadata

    from farm_notary.fingerprint import fingerprint_fields, numpy_build_info

    dists = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            dists.add(f"{name}=={dist.version}")
    env = {
        **fingerprint_fields(),
        "platform": platform.platform(),
        "packages_hash": hashlib.sha256(
            "\n".join(sorted(dists)).encode("utf-8")
        ).hexdigest(),
        "package_count": len(dists),
    }
    numpy_info = numpy_build_info()
    if numpy_info is not None:
        env["numpy"] = numpy_info
    if lockfile is not None:
        env["lockfile"] = Path(lockfile).name
        env["lockfile_sha256"] = hash_file(Path(lockfile))
    return env


@dataclass
class Manifest:
    schema: str = MANIFEST_VERSION
    farm_notary_version: Optional[str] = None
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
    # Allowlist patterns used to admit artifacts and the number of run-dir
    # files that matched nothing (visible omissions, not silent ones).
    publish_patterns: List[str] = field(default_factory=list)
    unmatched_count: int = 0
    official_record: dict = field(default_factory=dict)
    # Optional derivation rules copied from the experiment profile
    # (config.notary.derived_from).  Omitted from serialization when empty
    # so older manifests keep a stable content hash.
    derived_from: list = field(default_factory=list)
    precommit_hash: Optional[str] = None
    cid: Optional[str] = None
    cid_reachable: Optional[bool] = None
    cid_reachable_checked_utc: Optional[str] = None
    anchor: Optional[dict] = None
    # Optional minisign / SSH signature of content_hash.  Excluded from
    # content_hash itself (same as cid/anchor) so it can be stamped after.
    identity: Optional[dict] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Omit truly optional fields that are None so that loading an older
        # manifest (which does not contain e.g. ``precommit_hash``) does not
        # inject a ``null`` value and alter the recomputed content hash.
        _OMIT_IF_NONE = {
            "farm_notary_version",
            "precommit_hash",
            "cid",
            "cid_reachable",
            "cid_reachable_checked_utc",
            "anchor",
            "identity",
        }
        _OMIT_IF_EMPTY = {"derived_from"}
        return {
            k: v
            for k, v in d.items()
            if not (k in _OMIT_IF_NONE and v is None)
            and not (k in _OMIT_IF_EMPTY and not v)
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Manifest":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def content_hash(self) -> str:
        """Hash of the manifest body, excluding stamp fields.

        ``cid``, ``anchor``, and ``identity`` are applied after the hash is
        computed (upload, OTS, optional lab signature) and must not feed back
        into it.
        """
        body = self.to_dict()
        for key in _STAMP_KEYS:
            body.pop(key, None)
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
    publish_patterns: Optional[Sequence[str]] = None,
    config: Optional[Mapping[str, Any]] = None,
    git_sha: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    runner: Optional[str] = None,
    command: Optional[str] = None,
    environment: Optional[Mapping[str, Any]] = None,
    lockfile: Optional[Path] = None,
    official_record: Optional[Mapping[str, Any]] = None,
    precommit_path: Optional[Path] = None,
    derived_from: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Manifest:
    """Build a :class:`Manifest` for *run_dir*.

    *publish_patterns* is an explicit allowlist of glob patterns that gate
    which files are hashed, listed, and eligible for upload.  Nothing is
    included unless it matches at least one pattern.  Patterns may come from
    three places, merged in order (last write wins within a source; earlier
    sources take precedence):

    1. The ``notary.publish`` key of the run config (``config`` argument).
    2. The *publish_patterns* argument (e.g. from ``--publish`` CLI flags).

    If neither source supplies patterns, *build_manifest* raises
    :class:`ValueError` so the caller is forced to make an explicit decision
    about what to publish.
    """
    run_dir = Path(run_dir)

    # Collect publish patterns from config first, then CLI overrides.
    effective_patterns: List[str] = []
    if config:
        notary_section = config.get("notary", {})
        if isinstance(notary_section, dict):
            cfg_publish = notary_section.get("publish", [])
            if isinstance(cfg_publish, list):
                effective_patterns.extend(cfg_publish)
    if publish_patterns:
        effective_patterns.extend(publish_patterns)

    if not effective_patterns:
        raise ValueError(
            "No publish patterns declared.  Pass --publish <glob> on the CLI or add "
            '\'notary": {"publish": ["<glob>", ...]}\' to the run config.  '
            "Nothing is hashed or uploaded unless explicitly declared."
        )

    # Collect all candidate files to compute the unmatched count.
    all_candidates: List[Path] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name in NOTARY_FILE_NAMES or path.name.endswith(NOTARY_FILE_SUFFIXES):
            continue
        all_candidates.append(path)

    artifacts: list = []
    hashes: dict = {}
    for path in iter_artifact_paths(run_dir, effective_patterns):
        rel = path.relative_to(run_dir).as_posix()
        artifacts.append(rel)
        hashes[rel] = hash_file(path)

    unmatched = len(all_candidates) - len(artifacts)

    if unmatched > 0:
        warnings.warn(
            f"{unmatched} file(s) in {run_dir} matched no publish pattern and were excluded "
            f"from the manifest.  Use --publish or 'notary.publish' in the config to include them.",
            stacklevel=2,
        )

    if git_sha is None:
        git_sha, detected_dirty = detect_git_status()
        if git_dirty is None:
            git_dirty = detected_dirty

    rules: list = []
    if derived_from:
        rules = [dict(rule) for rule in derived_from]
    else:
        from farm_notary.derive import extract_derived_from

        rules = extract_derived_from(config)

    manifest = Manifest(
        farm_notary_version=TOOL_VERSION,
        created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_sha=git_sha,
        git_dirty=git_dirty,
        runner=runner,
        command=command,
        environment=dict(environment) if environment is not None else capture_environment(lockfile),
        config=dict(config or {}),
        artifacts=artifacts,
        artifact_hashes=hashes,
        publish_patterns=list(effective_patterns),
        unmatched_count=unmatched,
        official_record=dict(official_record or {}),
        derived_from=rules,
    )
    if precommit_path is not None:
        import shutil

        from farm_notary.precommit import (
            PRECOMMIT_NAME,
            PRECOMMIT_PROOF_NAME,
            load_precommit,
            precommit_hash as _pc_hash,
        )

        precommit_path = Path(precommit_path)
        pc = load_precommit(precommit_path)
        manifest.precommit_hash = _pc_hash(pc)

        # Ensure precommit.json is in the run directory — verify_precommit
        # always loads it from there.  Copy it (and its proof, if present)
        # when the caller supplied a path outside the run directory.
        target_pc = run_dir / PRECOMMIT_NAME
        if precommit_path.resolve() != target_pc.resolve():
            shutil.copy2(precommit_path, target_pc)
            src_proof = precommit_path.parent / PRECOMMIT_PROOF_NAME
            if src_proof.is_file():
                shutil.copy2(src_proof, run_dir / PRECOMMIT_PROOF_NAME)
    manifest.validate()
    return manifest


def write_manifest(manifest: Manifest, run_dir: Path) -> Path:
    dest = Path(run_dir) / MANIFEST_NAME
    dest.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest


def load_manifest(path: Path, *, validate: bool = True) -> Manifest:
    """Load manifest.json from a file path or a run directory."""
    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = Manifest.from_dict(data)
    if validate:
        manifest.validate()
    return manifest
