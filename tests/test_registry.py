import json

import pytest

from farm_notary.campaign import build_campaign
from farm_notary.cli import main
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.registry import (
    RegistryError,
    add_to_registry,
    entry_from_manifest,
    render_index,
    write_registry,
)


def _run(tmp_path, seed=0, name="consensus"):
    run = tmp_path / f"{name}-{seed}"
    run.mkdir()
    (run / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        runner=name,
        config={"seed": seed},
    )
    manifest.cid = f"bafy{name}{seed}"
    write_manifest(manifest, run)
    return run, manifest


def test_render_index_has_required_columns_and_no_scores():
    md = render_index(
        [
            {
                "experiment": "consensus",
                "seed": 0,
                "cid": "bafyabc",
                "claim_level": "bytes",
                "date": "2026-08-27",
            }
        ]
    )
    assert "Experiment" in md
    assert "Seed" in md
    assert "CID" in md
    assert "Claim" in md
    assert "Date" in md
    assert "bafyabc" in md
    assert "not** a leaderboard" in md.lower() or "not a leaderboard" in md.lower()
    # Disclaimer may mention scores as out of scope; the table must not rank.
    assert "| Score |" not in md
    assert "| Rank |" not in md
    header = [line for line in md.splitlines() if line.startswith("| Experiment")][0]
    assert header.count("|") == 6  # five columns plus fences


def test_forbidden_score_keys_rejected(tmp_path):
    with pytest.raises(RegistryError, match="scoreboard"):
        write_registry(
            [{"experiment": "x", "seed": 0, "cid": "c", "claim_level": "bytes", "date": "2026-01-01", "score": 9}],
            tmp_path / "reg",
        )


def test_add_and_upsert(tmp_path):
    run, manifest = _run(tmp_path)
    entry = entry_from_manifest(manifest, run)
    md, js, added = add_to_registry(tmp_path / "registry", [entry])
    assert added == 1
    assert md.is_file()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["schema"].startswith("farmnotary.registry")
    assert data["entries"][0]["cid"] == manifest.cid
    # upsert same content hash
    entry["claim_level"] = "bitwise"
    add_to_registry(tmp_path / "registry", [entry])
    data = json.loads(js.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1
    assert data["entries"][0]["claim_level"] == "bitwise"


def test_cli_index_from_run_and_campaign(tmp_path, capsys):
    run, _ = _run(tmp_path, seed=0)
    registry = tmp_path / "public"
    assert main(["index", "--registry", str(registry), "--run-dir", str(run)]) == 0
    capsys.readouterr()
    body = (registry / "index.md").read_text(encoding="utf-8")
    assert "consensus" in body
    assert "bafyconsensus0" in body

    other = [_run(tmp_path, seed=i, name="sweep")[0] for i in range(2)]
    camp = tmp_path / "camp"
    assert main(
        [
            "campaign",
            "--name",
            "sweep",
            "--run-dir",
            str(other[0]),
            "--run-dir",
            str(other[1]),
            "--out",
            str(camp),
        ]
    ) == 0
    capsys.readouterr()
    assert main(["index", "--registry", str(registry), "--campaign", str(camp)]) == 0
    body = (registry / "index.md").read_text(encoding="utf-8")
    assert "sweep" in body
    assert "| Score |" not in body


def test_campaign_entries(tmp_path):
    from farm_notary.registry import entries_from_campaign

    runs = [_run(tmp_path, seed=i)[0] for i in range(2)]
    campaign = build_campaign(runs, name="consensus", campaign_dir=tmp_path)
    rows = entries_from_campaign(campaign)
    assert len(rows) == 2
    assert {r["seed"] for r in rows} == {0, 1}
