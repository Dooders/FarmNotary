from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from farm_notary.schema import MANIFEST_VERSION, PRIVATE_NAME_FRAGMENTS, REQUIRED_KEYS


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_json(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def is_private_artifact(name: str) -> bool:
    lowered = name.lower()
    return any(frag in lowered for frag in PRIVATE_NAME_FRAGMENTS)


@dataclass
class Manifest:
    schema: str = MANIFEST_VERSION
    created_utc: str = ""
    git_sha: str | None = None
    runner: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    official_record: dict[str, Any] = field(default_factory=dict)
    cid: str | None = None
    chain: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def content_hash(self) -> str:
        body = self.to_dict()
        body.pop("cid", None)
        body.pop("chain", None)
        return hash_json(body)

    def validate(self) -> None:
        data = self.to_dict()
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"manifest missing keys: {missing}")


def build_manifest(
    run_dir: Path,
    *,
    config: Mapping[str, Any] | None = None,
    git_sha: str | None = None,
    runner: str | None = None,
    official_record: Mapping[str, Any] | None = None,
    patterns: tuple[str, ...] = ("*.csv", "*.json", "*.md"),
) -> Manifest:
    run_dir = Path(run_dir)
    artifacts: list[str] = []
    hashes: dict[str, str] = {}
    for pattern in patterns:
        for path in sorted(run_dir.glob(pattern)):
            if not path.is_file() or path.name == "manifest.json":
                continue
            if is_private_artifact(path.name):
                continue
            rel = path.name
            artifacts.append(rel)
            hashes[rel] = hash_file(path)
    manifest = Manifest(
        created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_sha=git_sha,
        runner=runner,
        config=dict(config or {}),
        artifacts=artifacts,
        artifact_hashes=hashes,
        official_record=dict(official_record or {}),
    )
    manifest.validate()
    return manifest


def write_manifest(manifest: Manifest, run_dir: Path) -> Path:
    dest = Path(run_dir) / "manifest.json"
    dest.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest
