"""Reproduce a notarized run and byte-compare the artifacts.

This is what turns "reproducible" from a claim into a procedure: re-run the
manifest's recorded command into a fresh directory, rehash every artifact the
manifest lists, and compare. A successful reproduction writes a receipt
(reproduction.json) that can itself be anchored via OpenTimestamps, giving
"independently reproduced" a timestamped, third-party-verifiable form.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from farm_notary.diagnose import (
    MismatchDiagnosis,
    diagnose_mismatches,
    format_diagnostics,
)
from farm_notary.manifest import (
    RECEIPT_NAME,
    Manifest,
    capture_environment,
    hash_file,
    hash_json,
    iter_artifact_paths,
)

RECEIPT_VERSION = "farmnotary.reproduction.v1"
RECEIPT_PROOF_NAME = "reproduction.ots"
RUN_DIR_PLACEHOLDER = "{run_dir}"


class ReproduceError(RuntimeError):
    pass


@dataclass
class ReproductionResult:
    command: str
    returncode: int
    matched: List[str] = field(default_factory=list)
    mismatched: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)
    ignore: List[str] = field(default_factory=list)
    diagnostics: List[MismatchDiagnosis] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.mismatched and not self.missing

    def summary(self) -> List[str]:
        lines = [
            f"command exit code: {self.returncode}",
            f"matched: {len(self.matched)} artifact(s) bitwise-identical",
        ]
        diagnosed = {item.artifact for item in self.diagnostics}
        if self.diagnostics:
            lines.extend(format_diagnostics(self.diagnostics))
        for name in self.mismatched:
            if name not in diagnosed:
                lines.append(f"mismatch: {name}")
        for name in self.missing:
            lines.append(f"missing from re-run: {name}")
        if self.ignore:
            lines.append(f"ignored globs (excluded from the claim): {', '.join(self.ignore)}")
        elif self.ignored:
            lines.append(f"ignored (excluded from the claim): {', '.join(self.ignored)}")
        if self.extra:
            lines.append(f"extra files in re-run (not compared): {', '.join(self.extra)}")
        return lines


def _is_ignored(name: str, ignore: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in ignore)


def reproduce_run(
    manifest: Manifest,
    *,
    fresh_dir: Optional[Path] = None,
    ignore: Sequence[str] = (),
    timeout: Optional[float] = None,
    original_dir: Optional[Path] = None,
) -> ReproductionResult:
    """Re-run the manifest's command into fresh_dir and compare artifact bytes.

    The recorded command must contain "{run_dir}", which is substituted with
    the fresh output directory. Comparison covers exactly the artifacts the
    manifest lists, minus any matching an ignore glob.
    """
    if not manifest.command:
        raise ReproduceError(
            "manifest records no command; re-create it with --command "
            f'(use "{RUN_DIR_PLACEHOLDER}" for the output directory)'
        )
    if RUN_DIR_PLACEHOLDER not in manifest.command:
        raise ReproduceError(
            f"recorded command has no {RUN_DIR_PLACEHOLDER} placeholder, so the "
            "re-run cannot be redirected to a fresh directory: "
            f"{manifest.command!r}"
        )
    fresh_dir = Path(fresh_dir) if fresh_dir else Path(tempfile.mkdtemp(prefix="farm-notary-repro-"))
    fresh_dir.mkdir(parents=True, exist_ok=True)
    command = manifest.command.replace(RUN_DIR_PLACEHOLDER, str(fresh_dir))

    proc = subprocess.run(command, shell=True, timeout=timeout)
    result = ReproductionResult(
        command=command, returncode=proc.returncode, ignore=list(ignore)
    )

    for name in sorted(manifest.artifact_hashes):
        if _is_ignored(name, ignore):
            result.ignored.append(name)
            continue
        path = fresh_dir / name
        if not path.is_file():
            result.missing.append(name)
        elif hash_file(path) == manifest.artifact_hashes[name]:
            result.matched.append(name)
        else:
            result.mismatched.append(name)

    if result.mismatched and original_dir is not None:
        result.diagnostics = diagnose_mismatches(
            result.mismatched, Path(original_dir), fresh_dir
        )

    listed = set(manifest.artifact_hashes)
    for path in iter_artifact_paths(fresh_dir, manifest.publish_patterns):
        rel = path.relative_to(fresh_dir).as_posix()
        if rel not in listed:
            result.extra.append(rel)

    return result


def build_receipt(manifest: Manifest, result: ReproductionResult) -> dict:
    """Reproduction receipt: who re-ran what, in which environment, and how it went.

    The receipt hash (hash_json of this dict) is what gets anchored.
    """
    return {
        "schema": RECEIPT_VERSION,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "original_manifest_hash": manifest.content_hash(),
        "command": manifest.command,
        "environment": capture_environment(),
        "ok": result.ok,
        "returncode": result.returncode,
        "matched": result.matched,
        "mismatched": result.mismatched,
        "missing": result.missing,
        "ignored": result.ignored,
        "ignore": list(result.ignore),
        "diagnostics": [item.to_dict() for item in result.diagnostics],
    }


def receipt_hash(receipt: dict) -> str:
    return hash_json(receipt)


def write_receipt(receipt: dict, run_dir: Path) -> Path:
    dest = Path(run_dir) / RECEIPT_NAME
    dest.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return dest


def load_receipt(run_dir: Path) -> dict:
    path = Path(run_dir) / RECEIPT_NAME
    return json.loads(path.read_text(encoding="utf-8"))
