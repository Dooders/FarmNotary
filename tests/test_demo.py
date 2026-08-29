"""Execute the live demo notebook and the tiny experiment it notarizes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from farm_notary.scope import ALLOWED_SENTENCE, CROSS_HARDWARE_NOT_A_CLAIM

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "docs" / "demo" / "farmnotary_live_demo.ipynb"
EXPERIMENT = ROOT / "docs" / "demo" / "experiment.py"


def _notebook_code() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return ["".join(cell["source"]) for cell in nb["cells"] if cell.get("cell_type") == "code"]


def test_notebook_has_executable_cells():
    cells = _notebook_code()
    assert len(cells) >= 8
    joined = "\n".join(cells)
    assert "notarize_run" in joined
    assert "evaluate_claims" in joined
    assert "reproduce_run" in joined


def test_experiment_writes_official_and_private_files(tmp_path: Path):
    out = tmp_path / "run"
    subprocess.run(
        [sys.executable, str(EXPERIMENT), "--seed", "0", "--out", str(out)],
        check=True,
    )
    for name in (
        "trials.csv",
        "summary.csv",
        "allocation_means.csv",
        "contrasts.csv",
        "REPORT.md",
        "run_config.json",
        "figure.png",
        "scratch_notes.txt",
        "private/ballots.csv",
    ):
        assert (out / name).is_file(), name
    header = (out / "private" / "ballots.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "voter,choice"
    subprocess.run(
        [sys.executable, str(EXPERIMENT), "verify", "--out", str(out)],
        check=True,
    )


def test_live_demo_notebook_executes():
    """Run every code cell in order. The demo is the product; it must work."""
    ns: dict = {"__name__": "__demo__"}
    cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        for i, source in enumerate(_notebook_code()):
            try:
                exec(compile(source, f"demo-cell-{i}", "exec"), ns)
            except Exception as exc:  # noqa: BLE001 — show which cell failed
                raise AssertionError(f"notebook code cell {i} failed: {exc}") from exc
    finally:
        os.chdir(cwd)

    card = ns["card"]
    assert card.ok
    assert card.tamper_evident == "pass"
    assert card.pre_specified == "precommit bound"
    status = card.bitwise_reproducible
    assert status != "missing"
    assert not status.startswith("fail")
    assert ALLOWED_SENTENCE in status or CROSS_HARDWARE_NOT_A_CLAIM in status
    assert "not claimed: scientific correctness" in card.render()

    manifest = ns["manifest"]
    assert "private/ballots.csv" not in manifest.artifacts
    assert "scratch_notes.txt" not in manifest.artifacts
    assert manifest.unmatched_count >= 1
    assert manifest.publish_profile == "consensus"

    result = ns["result"]
    assert result.ok
    assert "REPORT.md" in result.matched

    broken = ns["broken_result"]
    assert not broken.ok
    kinds = {kind for item in broken.diagnostics for kind in item.kinds}
    assert "embedded_absolute_path" in kinds

    derived = ns["problems"]
    assert derived == []
