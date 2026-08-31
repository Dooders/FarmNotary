"""Tests for farm_notary.plugins — MLflow and DVC integration hooks."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from farm_notary.manifest import MANIFEST_NAME
from farm_notary.interop import SLSA_FILE_NAME
from farm_notary.plugins import (
    MLflowNotaryPlugin,
    dvc_anchor_outputs,
    notarize_run,
    notarize_tracker_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    (d / "metrics.json").write_text('{"acc": 0.9}', encoding="utf-8")
    (d / "model.pkl").write_text("fake-pkl", encoding="utf-8")
    return d


def _fake_run(artifact_dir: Path, run_id: str = "run123") -> MagicMock:
    """Return a minimal mock that mimics mlflow.entities.Run."""
    info = MagicMock()
    info.artifact_uri = f"file://{artifact_dir}"
    info.run_id = run_id
    run = MagicMock()
    run.info = info
    data = MagicMock()
    data.params = {"lr": "0.01", "epochs": "10"}
    run.data = data
    return run


# ---------------------------------------------------------------------------
# MLflowNotaryPlugin
# ---------------------------------------------------------------------------


class TestMLflowNotaryPlugin:
    def test_writes_manifest_to_artifact_dir(self, tmp_path):
        artifact_dir = _make_artifact_dir(tmp_path)
        plugin = MLflowNotaryPlugin(publish_patterns=["*.json"])
        run = _fake_run(artifact_dir)
        path = plugin.on_run_end(run)
        assert path is not None
        assert path.name == MANIFEST_NAME
        assert path.exists()

    def test_manifest_includes_mlflow_run_id(self, tmp_path):
        artifact_dir = _make_artifact_dir(tmp_path)
        plugin = MLflowNotaryPlugin(publish_patterns=["*.json"])
        run = _fake_run(artifact_dir, run_id="myrun42")
        plugin.on_run_end(run)
        manifest_path = artifact_dir / MANIFEST_NAME
        data = json.loads(manifest_path.read_text())
        assert data["config"].get("mlflow_run_id") == "myrun42"

    def test_requires_publish_scope(self):
        with pytest.raises(ValueError, match="publish_patterns"):
            MLflowNotaryPlugin()

    def test_non_local_artifact_uri_returns_none(self, tmp_path):
        plugin = MLflowNotaryPlugin(publish_patterns=["*.json"])
        info = MagicMock()
        info.artifact_uri = "s3://bucket/path"
        info.run_id = "r1"
        run = MagicMock()
        run.info = info
        run.data = MagicMock()
        run.data.params = {}
        with pytest.warns(UserWarning, match="not a local path"):
            assert plugin.on_run_end(run) is None

    def test_missing_artifact_dir_returns_none(self, tmp_path):
        plugin = MLflowNotaryPlugin(publish_patterns=["*.json"])
        info = MagicMock()
        info.artifact_uri = f"file://{tmp_path / 'nonexistent'}"
        info.run_id = "r2"
        run = MagicMock()
        run.info = info
        run.data = MagicMock()
        run.data.params = {}
        assert plugin.on_run_end(run) is None

    def test_extra_config_merged(self, tmp_path):
        artifact_dir = _make_artifact_dir(tmp_path)
        plugin = MLflowNotaryPlugin(
            publish_patterns=["*.json"],
            config={"experiment": "baseline"},
        )
        run = _fake_run(artifact_dir)
        plugin.on_run_end(run)
        data = json.loads((artifact_dir / MANIFEST_NAME).read_text())
        assert data["config"].get("experiment") == "baseline"


# ---------------------------------------------------------------------------
# dvc_anchor_outputs
# ---------------------------------------------------------------------------

_DVC_LOCK_CONTENT = """\
schema: '2.0'
stages:
  preprocess:
    cmd: python preprocess.py
    deps:
    - path: data/raw.csv
      md5: aaaa
    outs:
    - path: data/processed.csv
      md5: bbbb
  train:
    cmd: python train.py
    deps:
    - path: data/processed.csv
      md5: bbbb
    outs:
    - path: models/model.pkl
      md5: cccc
"""


class TestDVCAnchorOutputs:
    def test_returns_none_when_no_dvc_lock(self, tmp_path):
        result = dvc_anchor_outputs(tmp_path)
        assert result is None

    def test_builds_manifest_from_dvc_lock(self, tmp_path):
        # Create the output files so build_manifest can hash them.
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "processed.csv").write_text("x\n1\n", encoding="utf-8")
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.pkl").write_text("fake", encoding="utf-8")

        lock_file = tmp_path / "dvc.lock"
        lock_file.write_text(_DVC_LOCK_CONTENT, encoding="utf-8")

        manifest = dvc_anchor_outputs(tmp_path, lock_file=lock_file)
        assert manifest is not None
        assert (tmp_path / MANIFEST_NAME).exists()

    def test_config_contains_dvc_outputs(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "processed.csv").write_text("x\n", encoding="utf-8")
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.pkl").write_text("y\n", encoding="utf-8")

        lock_file = tmp_path / "dvc.lock"
        lock_file.write_text(_DVC_LOCK_CONTENT, encoding="utf-8")

        manifest = dvc_anchor_outputs(tmp_path, lock_file=lock_file)
        assert manifest is not None
        dvc_outs = manifest.config.get("dvc_outputs", [])
        paths = [o["path"] for o in dvc_outs]
        assert "data/processed.csv" in paths
        assert "models/model.pkl" in paths

    def test_empty_lock_without_patterns_raises(self, tmp_path):
        (tmp_path / "dvc.lock").write_text("schema: '2.0'\nstages: {}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no default"):
            dvc_anchor_outputs(tmp_path)


# ---------------------------------------------------------------------------
# notarize_run helper
# ---------------------------------------------------------------------------


class TestNotarizeTrackerRun:
    def test_writes_manifest(self, tmp_path):
        (tmp_path / "result.csv").write_text("metric,value\nacc,0.9\n", encoding="utf-8")
        manifest = notarize_tracker_run(
            tmp_path,
            publish_patterns=["*.csv"],
            config={"trial": 1},
        )
        assert (tmp_path / MANIFEST_NAME).exists()
        assert "result.csv" in manifest.artifacts

    def test_requires_publish_scope(self, tmp_path):
        (tmp_path / "result.csv").write_text("x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="publish_patterns"):
            notarize_tracker_run(tmp_path)

    def test_old_name_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="notarize_tracker_run"):
            notarize_run(tmp_path, publish_patterns=["*.csv"])

    def test_emit_slsa_flag(self, tmp_path):
        (tmp_path / "result.csv").write_text("x\n1\n", encoding="utf-8")
        notarize_tracker_run(tmp_path, publish_patterns=["*.csv"], emit_slsa=True)
        assert (tmp_path / SLSA_FILE_NAME).exists()
        assert not (tmp_path / "slsa-provenance.json").exists()
