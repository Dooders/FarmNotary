"""Derivation claims: recompute named artifacts from their sources.

Byte-identity of a PNG is a renderer claim.  Reviewers of a paper figure
usually care that ``summary.csv`` / allocations / ``REPORT.md`` recompute
exactly from ``trials.csv``.  Experiment profiles declare that relationship
as ``notary.derived_from`` rules; verify runs them and records a derivation
claim even when figures are excluded from the bitwise set.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from farm_notary.manifest import hash_file

RUN_DIR_PLACEHOLDER = "{run_dir}"
SOURCE_PLACEHOLDER = "{source}"
OUTPUT_PLACEHOLDER = "{output}"

MODE_RECOMPUTE = "recompute"
MODE_VERIFY = "verify"
VALID_MODES = (MODE_RECOMPUTE, MODE_VERIFY)


class DeriveError(ValueError):
    pass


@dataclass
class DerivedFromRule:
    outputs: List[str]
    sources: List[str]
    command: str
    mode: str = MODE_RECOMPUTE

    def to_dict(self) -> dict:
        return {
            "outputs": list(self.outputs),
            "sources": list(self.sources),
            "command": self.command,
            "mode": self.mode,
        }


def normalize_rules(raw: Any) -> List[DerivedFromRule]:
    """Parse ``derived_from`` from an experiment profile or manifest."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise DeriveError("derived_from must be a list of rules")
    rules: List[DerivedFromRule] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise DeriveError(f"derived_from[{i}] must be an object")
        outputs = item.get("outputs") or item.get("output")
        sources = item.get("sources") or item.get("source")
        if isinstance(outputs, str):
            outputs = [outputs]
        if isinstance(sources, str):
            sources = [sources]
        if not outputs:
            raise DeriveError(f"derived_from[{i}] is missing outputs")
        if not sources:
            raise DeriveError(f"derived_from[{i}] is missing sources")
        command = item.get("command")
        if not command or not isinstance(command, str):
            raise DeriveError(f"derived_from[{i}] is missing command")
        mode = item.get("mode", MODE_RECOMPUTE)
        if mode not in VALID_MODES:
            raise DeriveError(
                f"derived_from[{i}] mode must be {MODE_RECOMPUTE!r} or {MODE_VERIFY!r}"
            )
        rules.append(
            DerivedFromRule(
                outputs=[str(o) for o in outputs],
                sources=[str(s) for s in sources],
                command=command,
                mode=mode,
            )
        )
    return rules


def extract_derived_from(config: Optional[Mapping[str, Any]]) -> List[dict]:
    """Pull ``notary.derived_from`` from a run / experiment config."""
    if not config:
        return []
    notary = config.get("notary")
    if not isinstance(notary, Mapping):
        return []
    rules = normalize_rules(notary.get("derived_from") or [])
    return [rule.to_dict() for rule in rules]


def _expand_command(command: str, run_dir: Path, rule: DerivedFromRule) -> str:
    expanded = command.replace(RUN_DIR_PLACEHOLDER, str(run_dir))
    if rule.sources:
        expanded = expanded.replace(SOURCE_PLACEHOLDER, str(run_dir / rule.sources[0]))
    if rule.outputs:
        expanded = expanded.replace(OUTPUT_PLACEHOLDER, str(run_dir / rule.outputs[0]))
    return expanded


def _check_listed_files(run_dir: Path, names: Sequence[str], *, kind: str, index: int) -> List[str]:
    problems: List[str] = []
    for name in names:
        if not (run_dir / name).is_file():
            problems.append(f"derived_from[{index}] {kind} missing: {name}")
    return problems


def verify_derived(
    manifest: Any,
    run_dir: Path,
    *,
    timeout: Optional[float] = None,
    allow_execute: bool = False,
) -> List[str]:
    """Run each ``derived_from`` rule.  Empty list means the claim holds.

    ``allow_execute`` must be set to *True* to permit running the
    manifest-supplied derivation commands.  Leaving it *False* (the default)
    skips execution and returns a warning instead, protecting callers (e.g.
    ``farm-notary verify``) from executing untrusted commands found in a
    downloaded manifest.
    """
    run_dir = Path(run_dir)
    raw = getattr(manifest, "derived_from", None)
    if not raw:
        config = getattr(manifest, "config", None) or {}
        try:
            raw = extract_derived_from(config)
        except DeriveError as exc:
            return [str(exc)]
    try:
        rules = normalize_rules(raw)
    except DeriveError as exc:
        return [str(exc)]
    if not rules:
        return []

    if not allow_execute:
        return [
            "derivation rules are declared but execution is disabled by default; "
            "pass allow_execute=True (or --verify-derived on the CLI) to run them"
        ]

    problems: List[str] = []
    for i, rule in enumerate(rules):
        problems.extend(_check_listed_files(run_dir, rule.sources, kind="source", index=i))
        problems.extend(_check_listed_files(run_dir, rule.outputs, kind="output", index=i))
        if any(p.startswith(f"derived_from[{i}]") for p in problems):
            continue
        if rule.mode == MODE_VERIFY:
            problems.extend(_run_verify_mode(rule, run_dir, index=i, timeout=timeout))
        else:
            problems.extend(_run_recompute_mode(rule, run_dir, index=i, timeout=timeout))
    return problems


def _run_verify_mode(
    rule: DerivedFromRule, run_dir: Path, *, index: int, timeout: Optional[float]
) -> List[str]:
    command = _expand_command(rule.command, run_dir, rule)
    proc = subprocess.run(command, shell=True, timeout=timeout, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        return [f"derived_from[{index}] verify command failed: {tail}"]
    return []


def _run_recompute_mode(
    rule: DerivedFromRule, run_dir: Path, *, index: int, timeout: Optional[float]
) -> List[str]:
    """Copy sources to a temp dir, re-run the command, compare output bytes."""
    expected = {name: hash_file(run_dir / name) for name in rule.outputs}
    with tempfile.TemporaryDirectory(prefix="farm-notary-derive-") as tmp:
        fresh = Path(tmp)
        for name in rule.sources:
            dest = fresh / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run_dir / name, dest)
        command = _expand_command(rule.command, fresh, rule)
        proc = subprocess.run(command, shell=True, timeout=timeout, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit {proc.returncode}"
            return [f"derived_from[{index}] recompute command failed: {tail}"]
        problems: List[str] = []
        for name, digest in expected.items():
            path = fresh / name
            if not path.is_file():
                problems.append(f"derived_from[{index}] did not produce: {name}")
                continue
            if hash_file(path) != digest:
                problems.append(f"derived_from[{index}] recompute mismatch: {name}")
        return problems


def derived_outputs(rules: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for rule in rules:
        for name in rule.get("outputs") or []:
            if name not in names:
                names.append(name)
    return names


def derived_sources(rules: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for rule in rules:
        for name in rule.get("sources") or []:
            if name not in names:
                names.append(name)
    return names
