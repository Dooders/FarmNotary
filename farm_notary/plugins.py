"""Tracker integration plugins.

Lightweight hooks that notarise experiment runs from popular ML tracking
frameworks without competing with their UIs.  FarmNotary does one thing:
produce a tamper-evident, anchored manifest of whatever artifacts the tracker
already recorded.

Allowlist-first: there is no default ``*``. Callers must pass
``publish_patterns`` or ``publish_profile``. Dirty trees are refused unless
``allow_dirty=True``. The helper here is :func:`notarize_tracker_run` — not
:func:`farm_notary.anchor.notarize_run`.

Neither plugin modifies the tracker's own data store — they only write
additional FarmNotary files alongside the artifacts.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _require_publish_scope(
    publish_patterns: Optional[Sequence[str]],
    publish_profile: Optional[str],
    *,
    what: str,
) -> None:
    if publish_patterns or publish_profile:
        return
    raise ValueError(
        f"{what} requires publish_patterns or publish_profile "
        "(allowlist-first; there is no default *)"
    )


# ---------------------------------------------------------------------------
# MLflow plugin
# ---------------------------------------------------------------------------


class MLflowNotaryPlugin:
    """Notarise an MLflow run when it completes.

    Usage (inside a training script or MLflow callback)::

        from farm_notary.plugins import MLflowNotaryPlugin
        import mlflow

        plugin = MLflowNotaryPlugin(publish_patterns=["*.json", "*.csv"])
        with mlflow.start_run() as run:
            # … training …
            plugin.on_run_end(run)

    The plugin writes ``manifest.json`` to the artifact directory of the
    given run.  Pass ``anchor=True`` to also call the OTS anchor step
    (dirty trees are refused unless ``allow_dirty=True``).
    """

    def __init__(
        self,
        *,
        publish_patterns: Optional[List[str]] = None,
        publish_profile: Optional[str] = None,
        git_sha: Optional[str] = None,
        anchor: bool = False,
        allow_dirty: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        _require_publish_scope(
            publish_patterns, publish_profile, what="MLflowNotaryPlugin"
        )
        self.publish_patterns = list(publish_patterns) if publish_patterns else None
        self.publish_profile = publish_profile
        self.git_sha = git_sha
        self.anchor = anchor
        self.allow_dirty = allow_dirty
        self.extra_config = config or {}

    def on_run_end(self, run: Any) -> Optional[Path]:
        """Notarise *run* by writing a manifest to its artifact directory.

        Parameters
        ----------
        run:
            An ``mlflow.entities.Run`` or any object with a ``info``
            attribute that exposes ``artifact_uri`` and ``run_id``.

        Returns
        -------
        Path or None
            Path to the written ``manifest.json``, or ``None`` if the artifact
            URI is not a local filesystem path.
        """
        from farm_notary.manifest import build_manifest, write_manifest

        artifact_uri: str = getattr(getattr(run, "info", run), "artifact_uri", "")
        run_id: str = getattr(getattr(run, "info", run), "run_id", "")

        # Only handle local file URIs; remote stores (S3, GCS, ftp, …) are
        # out of scope. Parse any URI scheme (including runs:/, mlflow-artifacts:/)
        # and warn when the scheme is not local.
        from urllib.parse import urlparse

        parsed = urlparse(artifact_uri)
        scheme = parsed.scheme
        is_windows_drive_path = (
            len(scheme) == 1
            and len(artifact_uri) >= 2
            and artifact_uri[0].isalpha()
            and artifact_uri[1] == ":"
        )
        if scheme and not is_windows_drive_path:
            if scheme == "file":
                from urllib.request import url2pathname

                file_path = parsed.path
                if parsed.netloc and parsed.netloc != "localhost":
                    file_path = f"//{parsed.netloc}{parsed.path}"
                artifact_uri = url2pathname(file_path)
            else:
                warnings.warn(
                    f"MLflow artifact URI is not a local path ({scheme}:); "
                    "skipping notarization",
                    UserWarning,
                    stacklevel=2,
                )
                return None
        artifact_path = Path(artifact_uri)
        if not artifact_path.is_dir():
            return None

        # Merge any MLflow params into the config if available.
        config: Dict[str, Any] = dict(self.extra_config)
        try:
            data = getattr(run, "data", None)
            if data is not None:
                config.update(getattr(data, "params", {}) or {})
        except Exception:  # noqa: BLE001
            pass
        if run_id:
            config["mlflow_run_id"] = run_id

        manifest = build_manifest(
            artifact_path,
            publish_patterns=self.publish_patterns,
            publish_profile=self.publish_profile,
            git_sha=self.git_sha,
            config=config,
        )
        path = write_manifest(manifest, artifact_path)

        if self.anchor:
            from farm_notary.anchor import anchor_run, write_proof
            from farm_notary.ots import OpenTimestampsBackend

            backend = OpenTimestampsBackend()
            receipt = anchor_run(
                manifest, backend=backend, allow_dirty=self.allow_dirty
            )
            write_proof(receipt, artifact_path)
            write_manifest(manifest, artifact_path)

        return path


# ---------------------------------------------------------------------------
# DVC plugin
# ---------------------------------------------------------------------------


def _parse_dvc_lock(lock_path: Path) -> List[Dict[str, Any]]:
    """Parse ``dvc.lock`` and return the list of stage output entries."""
    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    except ImportError:
        # Fall back to a minimal YAML subset (no anchors needed for dvc.lock).
        import re

        data = _simple_yaml_parse(lock_path.read_text(encoding="utf-8"))

    stages = data.get("stages", {})
    if not isinstance(stages, dict):
        stages = {}
    outputs: List[Dict[str, Any]] = []
    for stage_name, stage_data in stages.items():
        if not isinstance(stage_data, dict):
            continue
        for out in stage_data.get("outs", []):
            entry = dict(out) if isinstance(out, dict) else {"path": str(out)}
            entry["_stage"] = stage_name
            outputs.append(entry)
    return outputs


def _simple_yaml_parse(text: str) -> dict:
    """Very minimal YAML parser sufficient for ``dvc.lock`` key/value blocks.

    Handles only string values, nested dicts, and lists of dicts — enough
    for the ``stages.<name>.outs`` fields FarmNotary needs.  If a real YAML
    library is present, :func:`_parse_dvc_lock` uses it instead.
    """
    result: dict = {}
    stack: list = [result]
    indent_stack: list = [-1]

    def current() -> dict:
        return stack[-1]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()

        # Pop stack if indent decreased.
        while indent <= indent_stack[-1]:
            stack.pop()
            indent_stack.pop()

        # List item.
        if line.startswith("- ") or line == "-":
            content = line[2:].strip() if line.startswith("- ") else ""
            # Walk up the stack to find the nearest dict whose last key holds
            # a list (or whose last key was just set to an empty dict and
            # should become a list).  This handles the common dvc.lock pattern
            # where a list item appears at an indent equal to its parent key.
            target_list: Optional[list] = None
            for si in range(len(stack) - 1, -1, -1):
                node = stack[si]
                if isinstance(node, dict) and node:
                    last_key = list(node.keys())[-1]
                    if isinstance(node[last_key], list):
                        target_list = node[last_key]
                        break
                    if isinstance(node[last_key], dict) and not node[last_key]:
                        node[last_key] = []
                        target_list = node[last_key]
                        break
                elif isinstance(node, list):
                    target_list = node
                    break
            if target_list is None:
                continue
            new_item: Dict[str, Any] = {}
            target_list.append(new_item)
            stack.append(new_item)
            indent_stack.append(indent)
            if content and ":" in content:
                k, _, v = content.partition(":")
                new_item[k.strip()] = v.strip()
            continue

        # Key: value.
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            cur = current()
            if not isinstance(cur, dict):
                continue
            if value:
                cur[key] = value
            else:
                # Nested dict or list — we'll see on next iteration.
                cur[key] = {}
                stack.append(cur[key])
                indent_stack.append(indent)

    return result


def dvc_anchor_outputs(
    run_dir: Path,
    *,
    lock_file: Optional[Path] = None,
    extra_files: Optional[Sequence[str]] = None,
    git_sha: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    publish_patterns: Optional[List[str]] = None,
) -> "Optional[Any]":
    """Build a FarmNotary manifest that covers DVC pipeline outputs.

    Reads ``dvc.lock`` (or *lock_file*) to discover output paths, builds a
    manifest for those files in *run_dir*, and writes ``manifest.json``.

    Parameters
    ----------
    run_dir:
        Root directory that contains the DVC project (and ``dvc.lock``).
    lock_file:
        Path to ``dvc.lock``.  Defaults to ``<run_dir>/dvc.lock``.
    extra_files:
        Additional relative paths to include alongside DVC outputs.
    git_sha:
        Git commit SHA to record.  Detected from the environment if omitted.
    config:
        Experiment config dict to embed in the manifest.
    publish_patterns:
        Extra allowlist patterns appended to DVC output paths.  If the lock
        file lists no outputs, this (or the derived output paths) is
        required — there is no default ``*``.

    Returns
    -------
    Manifest or None
        The written manifest, or ``None`` if ``dvc.lock`` is absent.
    """
    from farm_notary.manifest import build_manifest, write_manifest

    rd = Path(run_dir)
    lf = lock_file or rd / "dvc.lock"
    if not lf.exists():
        return None

    outputs = _parse_dvc_lock(lf)
    dvc_paths = [out.get("path", "") for out in outputs if out.get("path")]

    # Build a config that captures the DVC lock md5/sha hashes for reference.
    cfg: Dict[str, Any] = dict(config or {})
    cfg["dvc_outputs"] = [
        {"path": o.get("path", ""), "md5": o.get("md5", o.get("hash", ""))}
        for o in outputs
        if o.get("path")
    ]

    patterns: List[str]
    if publish_patterns:
        patterns = list(publish_patterns)
    else:
        # Derive glob patterns from the concrete DVC output paths.
        # DVC commonly tracks entire directories (e.g. "models"); expand each
        # bare directory name to also match its descendants so that files like
        # "models/model.pkl" are included.
        expanded: List[str] = []
        for p in dvc_paths:
            expanded.append(p)
            # Add a recursive descendant pattern unless already a glob.
            if "*" not in p and "?" not in p:
                expanded.append(p.rstrip("/") + "/**/*")
        patterns = expanded + list(extra_files or [])

    if not patterns:
        raise ValueError(
            "dvc_anchor_outputs found no DVC outputs and no publish_patterns. "
            "Pass publish_patterns explicitly; there is no default * allowlist."
        )

    manifest = build_manifest(
        rd,
        publish_patterns=patterns,
        git_sha=git_sha,
        config=cfg,
    )
    write_manifest(manifest, rd)
    return manifest


# ---------------------------------------------------------------------------
# Shared tracker helper (not farm_notary.anchor.notarize_run)
# ---------------------------------------------------------------------------


def notarize_tracker_run(
    run_dir: Path,
    *,
    publish_patterns: Optional[List[str]] = None,
    publish_profile: Optional[str] = None,
    git_sha: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    anchor: bool = False,
    allow_dirty: bool = False,
    emit_slsa: bool = False,
) -> "Any":
    """Write a manifest (and optionally anchor + emit unsigned SLSA) for *run_dir*.

    Tracker-callback helper. The package export ``farm_notary.notarize_run``
    is still :func:`farm_notary.anchor.notarize_run`.

    Parameters
    ----------
    run_dir:
        Directory containing the run artifacts.
    publish_patterns:
        Allowlist patterns. Required unless *publish_profile* is set.
    publish_profile:
        Named profile (``consensus``, ``rl-sweep``, ``evolution-run``).
    git_sha:
        Git SHA to record.
    config:
        Experiment config dict.
    anchor:
        If True, stamp the manifest with OpenTimestamps.
    allow_dirty:
        Passed to :func:`farm_notary.anchor.anchor_run`. Default False.
    emit_slsa:
        If True, write ``slsa-provenance.unsigned.json`` alongside the
        manifest.

    Returns
    -------
    Manifest
        The written manifest.
    """
    from farm_notary.manifest import build_manifest, write_manifest

    _require_publish_scope(
        publish_patterns, publish_profile, what="notarize_tracker_run"
    )

    rd = Path(run_dir)
    manifest = build_manifest(
        rd,
        publish_patterns=publish_patterns,
        publish_profile=publish_profile,
        git_sha=git_sha,
        config=config or {},
    )
    write_manifest(manifest, rd)

    if anchor:
        from farm_notary.anchor import anchor_run, write_proof
        from farm_notary.ots import OpenTimestampsBackend

        backend = OpenTimestampsBackend()
        receipt = anchor_run(manifest, backend=backend, allow_dirty=allow_dirty)
        write_proof(receipt, rd)
        write_manifest(manifest, rd)

    if emit_slsa:
        from farm_notary.interop import emit_slsa as _emit_slsa

        _emit_slsa(manifest, rd)

    return manifest


def notarize_run(*args: Any, **kwargs: Any) -> Any:
    """Removed: this name collided with ``farm_notary.anchor.notarize_run``."""
    raise RuntimeError(
        "farm_notary.plugins.notarize_run was renamed to notarize_tracker_run. "
        "The package export farm_notary.notarize_run is still the anchor helper."
    )
