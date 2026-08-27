from __future__ import annotations

from pathlib import Path

from farm_notary.manifest import Manifest, hash_file


def verify_run_dir(manifest: Manifest, run_dir: Path) -> list[str]:
    problems: list[str] = []
    run_dir = Path(run_dir)
    for name, expected in manifest.artifact_hashes.items():
        path = run_dir / name
        if not path.is_file():
            problems.append(f"missing artifact: {name}")
            continue
        actual = hash_file(path)
        if actual != expected:
            problems.append(f"hash mismatch: {name}")
    return problems
