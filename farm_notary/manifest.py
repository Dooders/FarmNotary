from __future__ import annotations

import fnmatch
import hashlib
import json
import platform
import subprocess
import warnings
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Tuple

from farm_notary.schema import (
    MANIFEST_VERSION,
    PRIVATE_NAME_FRAGMENTS,
    REQUIRED_KEYS,
    TOOL_VERSION,
)

MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "reproduction.json"

# Notary metadata written next to the artifacts, never treated as artifacts.
NOTARY_FILE_NAMES = frozenset(
    {
        MANIFEST_NAME,
        RECEIPT_NAME,
        "precommit.json",
        "campaign.json",
        "appendix.md",
        "seeds.json",
        "provenance-chain.json",
        "ro-crate-metadata.json",
        "slsa-provenance.json",
        "slsa-provenance.unsigned.json",
        "c2pa-claim.json",
        "c2pa-claim-summary.unsigned.json",
    }
)
NOTARY_FILE_SUFFIXES = (".ots",)

# Seed keys stripped when hashing a sweep's shared config.
SEED_KEYS = ("seed", "rng_seed", "random_seed")


def extract_seed(config: Optional[Mapping[str, Any]]) -> Optional[Any]:
    if not config:
        return None
    for key in SEED_KEYS:
        if key in config:
            return config[key]
    return None


def config_hash_excluding_seed(config: Optional[Mapping[str, Any]]) -> str:
    """Hash of the experiment config with per-run seed keys removed.

    A sweep of seed 0…N should share this hash even though each child
    records a different seed.
    """
    body = {k: v for k, v in dict(config or {}).items() if k not in SEED_KEYS}
    return hash_json(body)

# Stamp fields that may be added after content_hash is computed.
_STAMP_KEYS = (
    "cid",
    "cid_reachable",
    "cid_reachable_checked_utc",
    "pin_service",
    "anchor",
    "identity",
)

# Sentinel that tells build_manifest to auto-detect CI provenance.
# Callers that pass ``ci_provenance=None`` explicitly opt out of detection.
_CI_PROV_AUTO = object()


def detect_ci_provenance() -> Optional[dict]:
    """Detect GitHub Actions CI provenance from environment variables.

    Returns a dict describing the attested CI context when the process is
    running inside GitHub Actions (``GITHUB_ACTIONS=true``), or ``None``
    when not in CI.  The ``sha`` key carries ``GITHUB_SHA`` — the commit
    that the runner checked out — which is later cross-checked against the
    manifest's ``git_sha`` by :func:`farm_notary.verify.verify_ci_provenance`.
    """
    import os

    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if not sha:
        return None
    prov: dict = {
        "kind": "github_actions",
        "sha": sha,
    }
    for key, var in (
        ("repository", "GITHUB_REPOSITORY"),
        ("ref", "GITHUB_REF"),
        ("workflow", "GITHUB_WORKFLOW"),
        ("run_id", "GITHUB_RUN_ID"),
    ):
        val = os.environ.get(var, "").strip()
        if val:
            prov[key] = val
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = prov.get("run_id", "")
    repo = prov.get("repository", "")
    if run_id and repo:
        prov["run_url"] = f"{server_url}/{repo}/actions/runs/{run_id}"
    return prov


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


def validate_run_path(name: str) -> None:
    """Reject paths that cannot name a contained POSIX run-directory file."""
    if "\0" in name:
        raise ValueError("path must not contain NUL")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be relative and must not contain '..'")


def resolve_run_path(run_dir: Path, name: str) -> Path:
    """Resolve *name* only when it remains within *run_dir*."""
    if not isinstance(name, str):
        raise ValueError("path must be a string")
    validate_run_path(name)
    root = Path(run_dir).resolve()
    path = (root / Path(*PurePosixPath(name).parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("path resolves outside the run directory") from exc
    return path


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


def iter_candidate_files(run_dir: Path) -> Iterator[Path]:
    """Yield non-hidden, non-notary, non-symlink files under *run_dir*."""
    run_dir = Path(run_dir)
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            warnings.warn(
                f"Skipping symlink {path.name!r}: symlinks are not followed to "
                "prevent hashing files outside the run directory.",
                UserWarning,
                stacklevel=2,
            )
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name in NOTARY_FILE_NAMES or path.name.endswith(NOTARY_FILE_SUFFIXES):
            continue
        yield path


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
    for path in iter_candidate_files(run_dir):
        rel_posix = path.relative_to(run_dir).as_posix()
        if not _matches_any_pattern(rel_posix, publish_patterns):
            continue
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


class DirtyTreeError(ValueError):
    """The git SHA does not identify the code; refuse to make a public claim."""


DIRTY_TREE_MESSAGE = (
    "git tree is dirty; the recorded sha does not identify the code. "
    "Commit first, or pass --allow-dirty to make an explicit exception."
)


def require_clean_identity(
    git_dirty: Optional[bool], *, allow_dirty: bool = False, cwd: Optional[Path] = None
) -> None:
    """Refuse a dirty tree unless the caller opted out of the code-identity claim.

    ``git_dirty is True`` means the SHA does not identify the code. Recording
    that flag is not enough — anchoring it would still let someone walk the
    science back. ``git_dirty is None`` is not a pass: detect the working
    tree so a caller who supplied only a SHA cannot skip the check.
    ``allow_dirty`` is the explicit exception.
    """
    if git_dirty is None:
        _, git_dirty = detect_git_status(cwd=cwd)
    if git_dirty and not allow_dirty:
        raise DirtyTreeError(DIRTY_TREE_MESSAGE)


def detect_git_status(cwd: Optional[Path] = None) -> Tuple[Optional[str], Optional[bool]]:
    """Return (HEAD sha, dirty flag), or (None, None) outside a repo.

    A dirty tree means the sha does not identify the code that actually ran,
    so it is recorded rather than hidden — and cannot be precommitted or
    anchored unless the caller passes ``allow_dirty``.
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


def resolve_git_identity(
    git_sha: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    cwd: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[bool]]:
    """Fill omitted sha / dirty from the working tree.

    A supplied SHA is not a substitute for the dirty check. Callers that
    pass ``git_sha`` without ``git_dirty`` still get a live detection.
    """
    if git_sha is None or git_dirty is None:
        detected_sha, detected_dirty = detect_git_status(cwd=cwd)
        if git_sha is None:
            git_sha = detected_sha
        if git_dirty is None:
            git_dirty = detected_dirty
    return git_sha, git_dirty


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
        "system": platform.system(),
        "machine": platform.machine(),
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
    # Named experiment-type profile that supplied the official-artifact
    # allowlist (consensus, rl-sweep, evolution-run). Optional so older
    # manifests that only recorded publish_patterns still load.
    publish_profile: Optional[str] = None
    unmatched_count: int = 0
    # Salted Merkle commitment over unpublished candidates. Omitted when
    # nothing was withheld so older bodies keep a stable content hash.
    withheld_salt: Optional[str] = None
    withheld_root: Optional[str] = None
    withheld_classes: Optional[dict] = None
    official_record: dict = field(default_factory=dict)
    # Optional derivation rules copied from the experiment profile
    # (config.notary.derived_from).  Omitted from serialization when empty
    # so older manifests keep a stable content hash.
    derived_from: list = field(default_factory=list)
    precommit_hash: Optional[str] = None
    # Beacon-derived seed binding (content-hashed; omitted when unused).
    beacon: Optional[dict] = None
    cid: Optional[str] = None
    cid_reachable: Optional[bool] = None
    cid_reachable_checked_utc: Optional[str] = None
    # "local" for a Kubo-only pin (lab convenience); a service name
    # (pinata, web3.storage, …) when --pin-remote was used. Stamp field.
    pin_service: Optional[str] = None
    anchor: Optional[dict] = None
    # Optional minisign / SSH signature of content_hash.  Excluded from
    # content_hash itself (same as cid/anchor) so it can be stamped after.
    identity: Optional[dict] = None
    # CI provenance captured from GitHub Actions environment variables at
    # manifest-build time.  Included in content_hash (not a stamp) so the
    # anchor commits to both the artifacts and the attested CI context.
    # ``None`` on local / developer runs; those manifests remain valid at a
    # lower claim level.
    ci_provenance: Optional[dict] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Omit truly optional fields that are None so that loading an older
        # manifest (which does not contain e.g. ``precommit_hash``) does not
        # inject a ``null`` value and alter the recomputed content hash.
        _OMIT_IF_NONE = {
            "farm_notary_version",
            "precommit_hash",
            "beacon",
            "publish_profile",
            "cid",
            "cid_reachable",
            "cid_reachable_checked_utc",
            "pin_service",
            "anchor",
            "identity",
            "ci_provenance",
            "withheld_salt",
            "withheld_root",
            "withheld_classes",
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
        if not isinstance(self.artifacts, list) or any(
            not isinstance(path, str) for path in self.artifacts
        ):
            raise ValueError("manifest field 'artifacts' must be a list of strings")
        for path in self.artifacts:
            try:
                validate_run_path(path)
            except ValueError as exc:
                raise ValueError(f"manifest artifact path {path!r} is invalid: {exc}") from exc
        if not isinstance(self.artifact_hashes, dict) or any(
            not isinstance(path, str) or not isinstance(digest, str)
            for path, digest in self.artifact_hashes.items()
        ):
            raise ValueError("manifest field 'artifact_hashes' must be an object of string values")
        if self.anchor is not None:
            if not isinstance(self.anchor, dict):
                raise ValueError("manifest field 'anchor' must be an object when present")
            detail = self.anchor.get("detail")
            if detail is not None:
                if not isinstance(detail, dict):
                    raise ValueError("manifest field 'anchor.detail' must be an object when present")
                proof = detail.get("proof")
                if proof is not None and not isinstance(proof, str):
                    raise ValueError("manifest field 'anchor.detail.proof' must be a string when present")
                if proof is not None:
                    try:
                        validate_run_path(proof)
                    except ValueError as exc:
                        raise ValueError(
                            f"manifest field 'anchor.detail.proof' is invalid: {exc}"
                        ) from exc
                binding_proof = detail.get("cid_binding_proof")
                if binding_proof is not None and not isinstance(binding_proof, str):
                    raise ValueError(
                        "manifest field 'anchor.detail.cid_binding_proof' must be a string when present"
                    )
                if binding_proof is not None:
                    try:
                        validate_run_path(binding_proof)
                    except ValueError as exc:
                        raise ValueError(
                            "manifest field 'anchor.detail.cid_binding_proof' "
                            f"is invalid: {exc}"
                        ) from exc
        if self.identity is not None and not isinstance(self.identity, dict):
            raise ValueError("manifest field 'identity' must be an object when present")
        self._validate_withheld()
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

    def _validate_withheld(self) -> None:
        present = [
            name
            for name, value in (
                ("withheld_salt", self.withheld_salt),
                ("withheld_root", self.withheld_root),
                ("withheld_classes", self.withheld_classes),
            )
            if value is not None
        ]
        if not present:
            return
        if len(present) != 3:
            raise ValueError(
                "withheld_salt, withheld_root, and withheld_classes must be "
                "recorded together"
            )
        if not isinstance(self.withheld_salt, str):
            raise ValueError("withheld_salt must be a hex string")
        if not isinstance(self.withheld_root, str):
            raise ValueError("withheld_root must be a hex string")
        try:
            salt = bytes.fromhex(self.withheld_salt)
            root = bytes.fromhex(self.withheld_root)
        except ValueError as exc:
            raise ValueError("withheld_salt and withheld_root must be hex") from exc
        if len(salt) != 32 or len(root) != 32:
            raise ValueError("withheld_salt and withheld_root must be 32 bytes")
        if not isinstance(self.withheld_classes, dict) or not self.withheld_classes:
            raise ValueError("withheld_classes must be a non-empty object")
        from farm_notary.withheld import class_counts_total

        for cls_name, spec in self.withheld_classes.items():
            if not isinstance(spec, dict):
                raise ValueError(
                    f"withheld_classes[{cls_name!r}] must be an object"
                )
            if (
                not isinstance(spec.get("count"), int)
                or isinstance(spec.get("count"), bool)
                or spec["count"] < 0
            ):
                raise ValueError(
                    f"withheld_classes[{cls_name!r}]['count'] must be a non-negative integer"
                )
            if not isinstance(spec.get("reason"), str):
                raise ValueError(
                    f"withheld_classes[{cls_name!r}]['reason'] must be a string"
                )
        if (
            not isinstance(self.unmatched_count, int)
            or isinstance(self.unmatched_count, bool)
            or self.unmatched_count < 0
        ):
            raise ValueError(
                "unmatched_count must be a non-negative integer, "
                f"got {self.unmatched_count!r}"
            )
        total = class_counts_total(self.withheld_classes)
        if total != self.unmatched_count:
            raise ValueError(
                "withheld_classes counts must sum to unmatched_count "
                f"({total} != {self.unmatched_count})"
            )


def build_manifest(
    run_dir: Path,
    *,
    publish_patterns: Optional[Sequence[str]] = None,
    publish_profile: Optional[str] = None,
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
    beacon: Optional[Mapping[str, Any]] = None,
    seed_index: Optional[int] = None,
    beacon_client: Optional[Any] = None,
    seeds_path: Optional[Path] = None,
    ci_provenance: Optional[Mapping[str, Any]] = _CI_PROV_AUTO,  # type: ignore[assignment]
    withheld_salt: Optional[str] = None,
) -> Manifest:
    """Build a :class:`Manifest` for *run_dir*.

    *publish_patterns* is an explicit allowlist of glob patterns that gate
    which files are hashed, listed, and eligible for upload.  Nothing is
    included unless it matches at least one pattern.  Patterns may come from
    three places, merged in order (later entries append; duplicates drop):

    1. A named experiment-type profile (``publish_profile``, else
       ``notary.profile`` in the run config): ``consensus``, ``rl-sweep``,
       or ``evolution-run``.
    2. The ``notary.publish`` key of the run config.
    3. The *publish_patterns* argument (e.g. from ``--publish`` CLI flags).

    If no source supplies patterns, *build_manifest* raises
    :class:`ValueError` so the caller is forced to make an explicit decision
    about what to publish.  Prefer a profile so labs do not invent globs.
    The resolved allowlist is recorded on the manifest as
    ``publish_patterns`` (and ``publish_profile`` when a profile was used)
    so the policy is part of the claim.
    """
    from farm_notary.profiles import resolve_publish_policy

    run_dir = Path(run_dir)

    resolved_profile, effective_patterns = resolve_publish_policy(
        profile=publish_profile,
        publish_patterns=publish_patterns,
        config=config,
    )

    if not effective_patterns:
        raise ValueError(
            "No publish patterns declared.  Pass --profile <name> "
            "(consensus, rl-sweep, evolution-run), or --publish <glob>, "
            "or add 'notary.profile' / 'notary.publish' to the run config.  "
            "Nothing is hashed or uploaded unless explicitly declared."
        )

    from farm_notary.withheld import classify_withheld, commit_withheld

    all_candidates = list(iter_candidate_files(run_dir))
    withheld_files = classify_withheld(
        run_dir,
        effective_patterns,
        is_private=is_private_path,
        matches_pattern=_matches_any_pattern,
        candidates=all_candidates,
    )
    commitment = commit_withheld(withheld_files, salt_hex=withheld_salt)

    artifacts: list = []
    hashes: dict = {}
    for path in iter_artifact_paths(run_dir, effective_patterns):
        rel_posix = path.relative_to(run_dir).as_posix()
        artifacts.append(rel_posix)
        hashes[rel_posix] = hash_file(path)

    unmatched = len(withheld_files)
    if unmatched != len(all_candidates) - len(artifacts):
        raise RuntimeError(
            "withheld set and published artifacts disagree; "
            "candidate classification drifted"
        )

    if unmatched > 0:
        denylist_n = 0
        unmatched_n = unmatched
        if commitment is not None:
            denylist_spec = commitment.classes.get("denylist")
            unmatched_spec = commitment.classes.get("unmatched")
            denylist_n = denylist_spec["count"] if denylist_spec else 0
            unmatched_n = unmatched_spec["count"] if unmatched_spec else 0
        warnings.warn(
            f"{unmatched} file(s) in {run_dir} were withheld from the official "
            f"record (denylist={denylist_n}, unmatched={unmatched_n}). "
            "Use --profile, --publish, or 'notary.publish' to include more. "
            "Names are not printed.",
            stacklevel=2,
        )

    git_sha, git_dirty = resolve_git_identity(git_sha, git_dirty)
    # Auto-detect GitHub Actions CI provenance when not explicitly supplied.
    # Pass ``ci_provenance=None`` to opt out of auto-detection.
    if ci_provenance is _CI_PROV_AUTO:
        ci_provenance = detect_ci_provenance()
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
        publish_profile=resolved_profile,
        unmatched_count=unmatched,
        withheld_salt=commitment.salt if commitment else None,
        withheld_root=commitment.root if commitment else None,
        withheld_classes=commitment.classes if commitment else None,
        official_record=dict(official_record or {}),
        derived_from=rules,
        beacon=dict(beacon) if beacon is not None else None,
        ci_provenance=dict(ci_provenance) if ci_provenance is not None else None,
    )
    if precommit_path is not None:
        import shutil

        from farm_notary.precommit import (
            PRECOMMIT_NAME,
            PRECOMMIT_PROOF_NAME,
            load_precommit,
        )
        from farm_notary.precommit import (
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
        default_seeds = precommit_path.parent / "seeds.json"
        if seed_index is not None:
            from farm_notary.beacon import bind_run_seed  # late: avoid import cycle

            plan = pc.get("seed_plan")
            if not plan:
                raise ValueError(
                    "--seed-index requires a precommit with a seed_plan "
                    "(pass --seed-count when running precommit)"
                )
            resolved_seeds = Path(seeds_path) if seeds_path is not None else default_seeds
            bound_config, bound_beacon = bind_run_seed(
                config=manifest.config,
                seed_plan=plan,
                seed_index=seed_index,
                client=beacon_client,
                seeds_path=resolved_seeds if resolved_seeds.is_file() else None,
            )
            manifest.config = bound_config
            manifest.beacon = bound_beacon
            if resolved_seeds.is_file():
                target_seeds = run_dir / "seeds.json"
                if resolved_seeds.resolve() != target_seeds.resolve():
                    shutil.copy2(resolved_seeds, target_seeds)
        elif seeds_path is None and default_seeds.is_file() and pc.get("seed_plan"):
            # Keep seeds.json next to the run when the lab already derived.
            target_seeds = run_dir / "seeds.json"
            if default_seeds.resolve() != target_seeds.resolve():
                shutil.copy2(default_seeds, target_seeds)
    elif seed_index is not None:
        raise ValueError("--seed-index requires --precommit")
    manifest.validate()
    return manifest


def list_withheld(run_dir: Path, publish_patterns: Sequence[str]):
    """Return withheld files for *run_dir* under the recorded allowlist."""
    from farm_notary.withheld import classify_withheld

    return classify_withheld(
        Path(run_dir),
        publish_patterns,
        is_private=is_private_path,
        matches_pattern=_matches_any_pattern,
        candidates=list(iter_candidate_files(run_dir)),
    )


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
