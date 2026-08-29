import sys
from pathlib import Path

from farm_notary.cli import main
import pytest

from farm_notary.derive import (
    DeriveError,
    extract_derived_from,
    normalize_rules,
    verify_derived,
)
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
    assert verify_derived(manifest, run, allow_execute=True) == []


def test_verify_derived_detects_stale_summary(tmp_path):
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    (run / "summary.csv").write_text("metric,value\nmean,9.9\n", encoding="utf-8")
    problems = verify_derived(manifest, run, allow_execute=True)
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
    assert verify_derived(manifest, run, allow_execute=True) == []
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    problems = verify_derived(manifest, run, allow_execute=True)
    assert any("verify command failed" in p for p in problems)


def test_verify_derived_disallowed_by_default(tmp_path):
    """verify_derived must not execute commands without allow_execute=True."""
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    problems = verify_derived(manifest, run)
    assert len(problems) == 1
    assert "allow_execute" in problems[0]


def test_cli_verify_skips_derivation_unless_flagged(tmp_path, capsys):
    """Missing is not failure: un-run derivation rules do not fail verify."""
    run, _, _ = _run_dir_with_derived(tmp_path)
    assert main(["verify", "--run-dir", str(run)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "statistics recompute exactly" not in out
    assert "--verify-derived" in out


def test_cli_verify_reports_derivation_claim(tmp_path, capsys):
    run, _, _ = _run_dir_with_derived(tmp_path)
    assert main(["verify", "--run-dir", str(run), "--verify-derived"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "statistics recompute exactly" in out


def test_cli_verify_fails_on_bad_derivation(tmp_path, capsys):
    run, _, _ = _run_dir_with_derived(tmp_path)
    (run / "summary.csv").write_text("metric,value\nmean,0\n", encoding="utf-8")
    assert main(["verify", "--run-dir", str(run), "--verify-derived"]) == 1
    assert "recompute mismatch" in capsys.readouterr().out


def test_normalize_rules_rejects_bad_shapes():
    with pytest.raises(DeriveError, match="list of rules"):
        normalize_rules({"outputs": ["a"]})
    with pytest.raises(DeriveError, match="must be an object"):
        normalize_rules(["not-an-object"])
    with pytest.raises(DeriveError, match="missing outputs"):
        normalize_rules([{"sources": ["a"], "command": "x"}])
    with pytest.raises(DeriveError, match="missing sources"):
        normalize_rules([{"outputs": ["a"], "command": "x"}])
    with pytest.raises(DeriveError, match="missing command"):
        normalize_rules([{"outputs": ["a"], "sources": ["b"]}])
    with pytest.raises(DeriveError, match="mode must be"):
        normalize_rules(
            [{"outputs": ["a"], "sources": ["b"], "command": "x", "mode": "maybe"}]
        )
    assert normalize_rules(None) == []
    assert extract_derived_from(None) == []
    assert extract_derived_from({"notary": "nope"}) == []


def test_verify_derived_reports_missing_source_and_output(tmp_path):
    run, manifest, _ = _run_dir_with_derived(tmp_path)
    (run / "trials.csv").unlink()
    problems = verify_derived(manifest, run, allow_execute=True)
    assert any("source missing: trials.csv" in p for p in problems)

    other = tmp_path / "other"
    other.mkdir()
    run, manifest, _ = _run_dir_with_derived(other)
    (run / "summary.csv").unlink()
    problems = verify_derived(manifest, run, allow_execute=True)
    assert any("output missing: summary.csv" in p for p in problems)


def test_recompute_reports_when_command_does_not_write_output(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "trials.csv").write_text("x\n", encoding="utf-8")
    (run / "summary.csv").write_text("old\n", encoding="utf-8")
    script = tmp_path / "noop.py"
    script.write_text("import sys\n", encoding="utf-8")
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        derived_from=[
            {
                "outputs": ["summary.csv"],
                "sources": ["trials.csv"],
                "command": f"{sys.executable} {script} {{run_dir}}",
                "mode": "recompute",
            }
        ],
    )
    problems = verify_derived(manifest, run, allow_execute=True)
    assert any("did not produce: summary.csv" in p for p in problems)


def test_placeholders_expand_source_and_output(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "trials.csv").write_text("x\n", encoding="utf-8")
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    script = tmp_path / "check.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.exit(0 if Path(sys.argv[1]).is_file() and Path(sys.argv[2]).is_file() else 1)\n",
        encoding="utf-8",
    )
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        derived_from=[
            {
                "outputs": ["summary.csv"],
                "sources": ["trials.csv"],
                "command": f"{sys.executable} {script} {{source}} {{output}}",
                "mode": "verify",
            }
        ],
    )
    assert verify_derived(manifest, run, allow_execute=True) == []
