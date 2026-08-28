"""Named experiment-type publish profiles."""

from pathlib import Path

import pytest

from farm_notary.manifest import build_manifest
from farm_notary.profiles import (
    PROFILE_NAMES,
    PUBLISH_PROFILES,
    get_profile,
    resolve_publish_policy,
)
from farm_notary.schema import PRIVATE_NAME_FRAGMENTS


def test_known_profiles_are_consensus_rl_sweep_evolution():
    assert tuple(PUBLISH_PROFILES) == PROFILE_NAMES
    for name in PROFILE_NAMES:
        profile = get_profile(name)
        assert profile.name == name
        assert profile.denylist == PRIVATE_NAME_FRAGMENTS
        assert "REPORT.md" in profile.patterns
        assert "run_config.json" in profile.patterns


def test_unknown_profile_lists_known_names():
    with pytest.raises(ValueError, match="rl-sweep"):
        get_profile("not-a-profile")


def test_consensus_profile_records_official_artifacts(tmp_path: Path):
    (tmp_path / "trials.csv").write_text("paradigm,trial\nparty,0\n", encoding="utf-8")
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    (tmp_path / "allocation_means.csv").write_text("paradigm,mean\nparty,0.2\n", encoding="utf-8")
    (tmp_path / "REPORT.md").write_text("# report\n", encoding="utf-8")
    (tmp_path / "run_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "welfare.png").write_bytes(b"png")
    (tmp_path / "scratch.log").write_text("noise\n", encoding="utf-8")
    (tmp_path / "votes_ballot.csv").write_text("secret\n", encoding="utf-8")

    manifest = build_manifest(tmp_path, publish_profile="consensus", git_sha="abc")
    assert manifest.publish_profile == "consensus"
    assert manifest.publish_patterns == list(PUBLISH_PROFILES["consensus"].patterns)
    assert set(manifest.artifacts) == {
        "trials.csv",
        "summary.csv",
        "allocation_means.csv",
        "REPORT.md",
        "run_config.json",
        "figures/welfare.png",
    }
    assert "scratch.log" not in manifest.artifacts
    assert "votes_ballot.csv" not in manifest.artifacts


def test_profile_denylist_still_blocks_private_names(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "voter_map.png").write_bytes(b"png")
    manifest = build_manifest(tmp_path, publish_profile="consensus", git_sha="abc")
    assert "summary.csv" in manifest.artifacts
    assert "voter_map.png" not in manifest.artifacts


def test_extra_publish_globs_append_to_profile(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "extra.json").write_text("{}\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        publish_profile="rl-sweep",
        publish_patterns=["extra.json"],
        git_sha="abc",
    )
    assert manifest.publish_profile == "rl-sweep"
    assert "extra.json" in manifest.publish_patterns
    assert list(PUBLISH_PROFILES["rl-sweep"].patterns) == manifest.publish_patterns[
        : len(PUBLISH_PROFILES["rl-sweep"].patterns)
    ]
    assert set(manifest.artifacts) == {"summary.csv", "extra.json"}


def test_profile_from_config(tmp_path: Path):
    (tmp_path / "fitness.csv").write_text("gen,fit\n0,1\n", encoding="utf-8")
    (tmp_path / "REPORT.md").write_text("# evo\n", encoding="utf-8")
    config = {"notary": {"profile": "evolution-run"}, "pop": 32}
    manifest = build_manifest(tmp_path, config=config, git_sha="abc")
    assert manifest.publish_profile == "evolution-run"
    assert "fitness.csv" in manifest.artifacts
    assert "REPORT.md" in manifest.artifacts


def test_cli_profile_overrides_config_profile():
    name, patterns = resolve_publish_policy(
        profile="consensus",
        config={"notary": {"profile": "rl-sweep", "publish": ["extra.csv"]}},
    )
    assert name == "consensus"
    assert patterns[0] == "trials.csv"
    assert "extra.csv" in patterns
    assert "metrics.csv" not in patterns


def test_resolve_dedupes_repeated_globs():
    _, patterns = resolve_publish_policy(
        profile="consensus",
        publish_patterns=["REPORT.md", "summary.csv"],
    )
    assert patterns.count("REPORT.md") == 1
    assert patterns.count("summary.csv") == 1


def test_old_manifest_without_publish_profile_omits_the_field(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    assert manifest.publish_profile is None
    assert "publish_profile" not in manifest.to_dict()
