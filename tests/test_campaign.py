import shutil
from pathlib import Path

from farm_notary.campaign import (
    build_campaign,
    config_hash_excluding_seed,
    extract_seed,
    load_campaign,
    verify_campaign,
    write_campaign,
)
from farm_notary.cli import main
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.schema import CAMPAIGN_VERSION


def _child(tmp_path: Path, name: str, seed: int, extra=None) -> Path:
    run = tmp_path / name
    run.mkdir()
    (run / "summary.csv").write_text(f"seed,{seed}\n", encoding="utf-8")
    config = {"seed": seed, "trials": 100, "voters": 300}
    if extra:
        config.update(extra)
    manifest = build_manifest(
        run, publish_patterns=["*.csv"], git_sha="abc", config=config, runner="consensus"
    )
    manifest.cid = f"bafy{name}"
    write_manifest(manifest, run)
    return run


def test_config_hash_ignores_seed():
    a = config_hash_excluding_seed({"seed": 0, "trials": 100})
    b = config_hash_excluding_seed({"seed": 7, "trials": 100})
    c = config_hash_excluding_seed({"seed": 0, "trials": 99})
    assert a == b
    assert a != c
    assert extract_seed({"rng_seed": 3}) == 3


def test_build_campaign_lists_children(tmp_path):
    children = [_child(tmp_path, f"seed-{i}", i) for i in range(3)]
    campaign = build_campaign(children, name="consensus-sweep", campaign_dir=tmp_path)
    assert campaign.schema == CAMPAIGN_VERSION
    assert campaign.name == "consensus-sweep"
    assert len(campaign.runs) == 3
    assert [r["seed"] for r in campaign.runs] == [0, 1, 2]
    assert campaign.config_hash
    assert all(r["config_hash"] == campaign.config_hash for r in campaign.runs)
    assert all(r["cid"].startswith("bafy") for r in campaign.runs)
    assert all(r["content_hash"] for r in campaign.runs)


def test_campaign_round_trip_and_verify(tmp_path):
    children = [_child(tmp_path, f"s{i}", i) for i in (0, 1)]
    campaign = build_campaign(children, name="sweep", campaign_dir=tmp_path)
    dest = write_campaign(campaign, tmp_path)
    loaded = load_campaign(tmp_path)
    assert loaded.content_hash() == campaign.content_hash()
    assert dest.name == "campaign.json"
    assert verify_campaign(loaded, tmp_path) == []


def test_verify_campaign_detects_child_tamper(tmp_path):
    children = [_child(tmp_path, f"s{i}", i) for i in (0, 1)]
    campaign = build_campaign(children, name="sweep", campaign_dir=tmp_path)
    write_campaign(campaign, tmp_path)
    (children[0] / "summary.csv").write_text("tampered\n", encoding="utf-8")
    from farm_notary.manifest import build_manifest, write_manifest

    # Rewrite the child manifest so load succeeds but the hash diverges.
    write_manifest(
        build_manifest(children[0], publish_patterns=["*.csv"], git_sha="abc", config={"seed": 0, "trials": 100}),
        children[0],
    )
    problems = verify_campaign(load_campaign(tmp_path), tmp_path)
    assert any("content_hash mismatch" in p for p in problems)


def test_verify_campaign_config_hash_mismatch(tmp_path):
    a = _child(tmp_path, "a", 0)
    b = _child(tmp_path, "b", 1, extra={"trials": 50})
    campaign = build_campaign([a, b], name="mixed", campaign_dir=tmp_path)
    # Force a parent hash that only matches the first child.
    campaign.config_hash = campaign.runs[0]["config_hash"]
    problems = verify_campaign(campaign, tmp_path)
    assert any("config_hash" in p for p in problems)


def test_content_hash_excludes_stamp_fields(tmp_path):
    children = [_child(tmp_path, "s0", 0)]
    campaign = build_campaign(children, name="one")
    before = campaign.content_hash()
    campaign.cid = "bafycampaign"
    campaign.anchor = {"backend": "dry-run"}
    campaign.identity = {"scheme": "ssh", "signature": "x"}
    assert campaign.content_hash() == before


def test_cli_campaign_and_verify(tmp_path, capsys):
    children = [_child(tmp_path, f"s{i}", i) for i in range(2)]
    out = tmp_path / "sweep"
    assert (
        main(
            [
                "campaign",
                "--name",
                "consensus",
                "--run-dir",
                str(children[0]),
                "--run-dir",
                str(children[1]),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "runs 2" in printed
    assert "config_hash" in printed
    assert main(["verify", "--campaign", str(out)]) == 0
    campaign_out = capsys.readouterr().out
    assert "child run" in campaign_out
    assert "claim card" not in campaign_out

    assert main(["verify", "--run-dir", str(out)]) == 0
    auto = capsys.readouterr().out
    assert "child run" in auto
    assert "claim card" not in auto


def test_cli_campaign_missing_child_manifest(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        main(["campaign", "--run-dir", str(empty), "--out", str(tmp_path / "sweep")])
        == 2
    )
    assert "missing manifest.json" in capsys.readouterr().err


def test_verify_campaign_require_local_when_child_gone(tmp_path):
    children = [_child(tmp_path, f"s{i}", i) for i in (0, 1)]
    campaign = build_campaign(children, name="sweep", campaign_dir=tmp_path)
    write_campaign(campaign, tmp_path)
    shutil.rmtree(children[0])
    assert verify_campaign(campaign, tmp_path) == []
    problems = verify_campaign(campaign, tmp_path, require_local=True)
    assert any("local run not present" in p for p in problems)
    assert main(["verify", "--campaign", str(tmp_path), "--require-local"]) == 1


def test_verify_campaign_catches_artifact_tamper_without_rewrite(tmp_path):
    children = [_child(tmp_path, f"s{i}", i) for i in (0, 1)]
    campaign = build_campaign(children, name="sweep", campaign_dir=tmp_path)
    write_campaign(campaign, tmp_path)
    (children[0] / "summary.csv").write_text("tampered-bytes\n", encoding="utf-8")
    problems = verify_campaign(load_campaign(tmp_path), tmp_path)
    assert any("artifact hash mismatch" in p for p in problems)
