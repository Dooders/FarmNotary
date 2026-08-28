import sys
from pathlib import Path

from farm_notary.cli import main
from farm_notary.derive import extract_derived_from, normalize_rules, verify_derived
from farm_notary.manifest import build_manifest, write_manifest

RECOMPUTE = """
import csv
import sys
from pathlib import Path
run = Path(sys.argv[1])
rows = list(csv.DictReader((run / "trials.csv").open(encoding="utf-8")))
mean = sum(float(r["value"]) for r in rows) / len(rows)
(run / "summary.csv").write_text(f"metric,value\\nmean,{mean}\\n", encoding="utf-8")
"""


def _write_recompute(tmp_path: Path) -> Path:
    script = tmp_path / "recompute.py"
    script.write_text(RECOMPUTE, encoding="utf-8")
    return script


def _run_dir_with_derived(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "trials.csv").write_text("trial,value\n0,1.5\n1,2.5\n", encoding="utf-8")
    (run / "summary.csv").write_text("metric,value\nmean,2.0\n", encoding="utf-8")
    (run / "figure.png").write_text("renderer-bytes", encoding="utf-8")
    script = _write_recompute(tmp_path)
    command = f"{sys.executable} {script} {{run_dir}}"
    config = {
        "seed": 0,
        "notary": {
            "publish": ["*.csv", "*.png"],
            "derived_from": [
                {
                    "outputs": ["summary.csv"],
                    "sources": ["trials.csv"],
                    "command": command,
                    "mode": "recompute",
                }
            ],
        },
    }
    manifest = build_manifest(run, config=config, git_sha="abc")
    write_manifest(manifest, run)
    return run, manifest, command


def test_extract_derived_from_profile():
    rules = extract_derived_from(
        {
            "notary": {
                "derived_from": [
                    {"outputs": "summary.csv", "sources": "trials.csv", "command": "x"}
                ]
            }
        }
    )
    assert rules[0]["outputs"] == ["summary.csv"]
    assert rules[0]["sources"] == ["trials.csv"]
    assert normalize_rules(rules)[0].mode == "recompute"


def test_build_manifest_copies_derived_from(tmp_path):
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    assert manifest.derived_from
    assert manifest.derived_from[0]["outputs"] == ["summary.csv"]
    assert "figure.png" in manifest.artifacts


def test_verify_derived_recomputes_exactly(tmp_path):
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    assert verify_derived(manifest, run) == []


def test_verify_derived_detects_stale_summary(tmp_path):
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    (run / "summary.csv").write_text("metric,value\nmean,9.9\n", encoding="utf-8")
    problems = verify_derived(manifest, run)
    assert any("recompute mismatch: summary.csv" in p for p in problems)


def test_verify_mode_uses_exit_code(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "trials.csv").write_text("x\n", encoding="utf-8")
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    script = tmp_path / "check.py"
    script.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        derived_from=[
            {
                "outputs": ["summary.csv"],
                "sources": ["trials.csv"],
                "command": f"{sys.executable} {script}",
                "mode": "verify",
            }
        ],
    )
    assert verify_derived(manifest, run) == []
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    problems = verify_derived(manifest, run)
    assert any("verify command failed" in p for p in problems)


def test_cli_verify_reports_derivation_claim(tmp_path, capsys):
    run, _, _ = _run_dir_with_derived(tmp_path)
    assert main(["verify", "--run-dir", str(run)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "statistics recompute exactly" in out


def test_cli_verify_fails_on_bad_derivation(tmp_path, capsys):
    run, _, _ = _run_dir_with_derived(tmp_path)
    (run / "summary.csv").write_text("metric,value\nmean,0\n", encoding="utf-8")
    assert main(["verify", "--run-dir", str(run)]) == 1
    assert "recompute mismatch" in capsys.readouterr().out
