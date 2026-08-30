"""Tracker integration plugins.

Lightweight hooks that notarise experiment runs from popular ML tracking
frameworks without competing with their UIs.  FarmNotary does one thing:
produce a tamper-evident, anchored manifest of whatever artifacts the tracker
already recorded.

MLflow
------
:class:`MLflowNotaryPlugin` listens for run completion and calls
:func:`~farm_notary.manifest.build_manifest` on the artifact directory.

DVC
---
:func:`dvc_anchor_outputs` reads ``dvc.lock`` (or a list of file paths) and
returns a manifest that covers the pipeline outputs, ready for anchoring.

Neither plugin modifies the tracker's own data store — they only write
additional FarmNotary files alongside the artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


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
    given run.  Pass ``anchor=True`` to also call the OTS anchor step.
    """

    def __init__(
        self,
        *,
        publish_patterns: Optional[List[str]] = None,
        git_sha: Optional[str] = None,
        anchor: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.publish_patterns = publish_patterns or ["*", "**/*"]
        self.git_sha = git_sha
        self.anchor = anchor
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
        # out of scope.  Use urlparse + url2pathname for correct cross-platform
        # handling of file:///C:/... paths on Windows.
        if artifact_uri.startswith("file://"):
            from urllib.parse import urlparse
            from urllib.request import url2pathname

            parsed = urlparse(artifact_uri)
            artifact_uri = url2pathname(parsed.path)
        elif "://" in artifact_uri:
            # Non-local URI (e.g. s3://, gs://, ftp://) — skip.
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
            git_sha=self.git_sha,
            config=config,
        )
        path = write_manifest(manifest, artifact_path)

        if self.anchor:
            from farm_notary.ots import stamp_manifest

            stamp_manifest(manifest, artifact_path)

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
    outputs: List[Dict[str, Any]] = []
    for stage_name, stage_data in stages.items():
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
    import re

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
        if line.startswith("- "):
            content = line[2:].strip()
            parent = current()
            if isinstance(parent, dict):
                # Find the last list in this dict.
                for key in reversed(list(parent.keys())):
                    if isinstance(parent[key], list):
                        parent[key].append({})
                        stack.append(parent[key][-1])
                        indent_stack.append(indent)
                        # Inline key: value after "- "
                        if ":" in content:
                            k, _, v = content.partition(":")
                            stack[-1][k.strip()] = v.strip()
                        break
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
        # list marker without item
        elif line == "-":
            cur = current()
            for key in reversed(list(cur.keys())):
                if isinstance(cur[key], dict) and not cur[key]:
                    cur[key] = []
                    break

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
        Allowlist patterns.  If not supplied, only DVC output paths are used.

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
        patterns = dvc_paths + list(extra_files or [])

    if not patterns:
        patterns = ["*", "**/*"]

    manifest = build_manifest(
        rd,
        publish_patterns=patterns,
        git_sha=git_sha,
        config=cfg,
    )
    write_manifest(manifest, rd)
    return manifest


# ---------------------------------------------------------------------------
# Shared notarize helper
# ---------------------------------------------------------------------------


def notarize_run(
    run_dir: Path,
    *,
    publish_patterns: Optional[List[str]] = None,
    git_sha: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    anchor: bool = False,
    emit_slsa: bool = False,
) -> "Any":
    """Write a manifest (and optionally anchor + emit SLSA) for *run_dir*.

    A convenience wrapper usable from any callback or post-processing hook.

    Parameters
    ----------
    run_dir:
        Directory containing the run artifacts.
    publish_patterns:
        Allowlist patterns.  Defaults to ``["*", "**/*"]``.
    git_sha:
        Git SHA to record.
    config:
        Experiment config dict.
    anchor:
        If True, stamp the manifest with OpenTimestamps.
    emit_slsa:
        If True, write a ``slsa-provenance.json`` alongside the manifest.

    Returns
    -------
    Manifest
        The written manifest.
    """
    from farm_notary.manifest import build_manifest, write_manifest

    rd = Path(run_dir)
    manifest = build_manifest(
        rd,
        publish_patterns=publish_patterns or ["*", "**/*"],
        git_sha=git_sha,
        config=config or {},
    )
    write_manifest(manifest, rd)

    if anchor:
        from farm_notary.ots import stamp_manifest

        stamp_manifest(manifest, rd)

    if emit_slsa:
        from farm_notary.interop import emit_slsa as _emit_slsa

        _emit_slsa(manifest, rd)

    return manifest
