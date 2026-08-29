"""Beacon-derived seeds (issue #30). CI uses FixedBeacon only."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from farm_notary.beacon import (
    BeaconError,
    DrandHttpClient,
    FixedBeacon,
    derive_seed_v1,
    derive_seeds,
    subset_note,
    verify_beacon_binding,
    write_fixed_beacon_fixture,
)
from farm_notary.campaign import build_campaign, campaign_seed_coverage_note
from farm_notary.cli import main
from farm_notary.ladder import L3_NEXT_MEANING, evaluate_ladder
from farm_notary.manifest import (
    build_manifest,
    config_hash_excluding_seed,
    write_manifest,
)
from farm_notary.ots import serialize_proof
from farm_notary.precommit import (
    PRECOMMIT_NAME,
    PRECOMMIT_PROOF_NAME,
    build_precommit,
    precommit_hash,
    write_precommit,
)
from farm_notary.verify import evaluate_claims, verify_precommit
from tests.test_ladder import attach_bitcoin
from tests.test_ots import pending_timestamp

CHAIN = "test-chain"
GENESIS = 1_700_000_000
PERIOD = 3
LATEST = 50
MIN_ROUND = 51
RANDOM_50 = bytes.fromhex("aa" * 32)
RANDOM_51 = bytes.fromhex("bb" * 32)


def _client(**rounds) -> FixedBeacon:
    data = {LATEST: RANDOM_50, MIN_ROUND: RANDOM_51}
    data.update(rounds)
    return FixedBeacon(
        chain_hash=CHAIN,
        genesis_time=GENESIS,
        period=PERIOD,
        rounds=data,
        latest_round=LATEST,
    )


def _plan(client, count=4, inclusion="all_in_campaign"):
    return build_precommit(
        config={"trials": 100},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=count,
        inclusion=inclusion,
        beacon_client=client,
    )["seed_plan"]


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return run


def _attach_precommit_proof(run_dir: Path, pc: dict) -> None:
    digest = bytes.fromhex(precommit_hash(pc))
    (run_dir / PRECOMMIT_PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, "https://example.com"))
    )


def test_sha256_v1_is_stable():
    material = {
        "chain_hash": CHAIN,
        "config_hash": "deadbeef",
        "index": 2,
        "round": MIN_ROUND,
    }
    expected = int.from_bytes(
        hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + RANDOM_51
        ).digest()[:8],
        "big",
    )
    seed = derive_seed_v1(
        chain_hash=CHAIN,
        round=MIN_ROUND,
        index=2,
        config_hash="deadbeef",
        randomness=RANDOM_51,
    )
    assert seed == expected
    assert seed == derive_seed_v1(
        chain_hash=CHAIN,
        round=MIN_ROUND,
        index=2,
        config_hash="deadbeef",
        randomness=RANDOM_51,
    )


def test_fixed_beacon_derive_count_and_mutation():
    client = _client()
    plan = _plan(client)
    seeds = derive_seeds(plan, {"trials": 100}, RANDOM_51)
    assert len(seeds) == 4
    assert seeds == derive_seeds(plan, {"trials": 100, "seed": 99}, RANDOM_51)
    assert seeds != derive_seeds(plan, {"trials": 100}, RANDOM_50)
    mutated_round = dict(plan)
    mutated_round["min_round"] = 99
    other = derive_seeds(mutated_round, {"trials": 100}, RANDOM_51, round=99)
    assert other != seeds


def test_precommit_rejects_seed_in_config_with_plan():
    client = _client()
    with pytest.raises(ValueError, match="must not contain a seed"):
        build_precommit(
            config={"trials": 1, "seed": 0},
            git_sha="abc",
            git_dirty=False,
            seed_count=2,
            inclusion="all_in_campaign",
            beacon_client=client,
        )


def test_precommit_seed_plan_from_fixed_beacon():
    client = _client()
    pc = build_precommit(
        config={"trials": 5},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=3,
        inclusion="primary_endpoint",
        delay_rounds=1,
        beacon_client=client,
    )
    plan = pc["seed_plan"]
    assert plan["count"] == 3
    assert plan["min_round"] == MIN_ROUND
    assert plan["derivation"] == "sha256-v1"
    assert plan["inclusion"] == "primary_endpoint"
    assert plan["chain_hash"] == CHAIN


def test_verify_precommit_allows_seed_only_diff_with_plan(tmp_path):
    client = _client()
    run = _run_dir(tmp_path)
    pc = build_precommit(
        config={"trials": 3},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=2,
        inclusion="all_in_campaign",
        beacon_client=client,
    )
    write_precommit(pc, run / PRECOMMIT_NAME)
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="python run.py {run_dir}",
        config={"trials": 3},
        precommit_path=run / PRECOMMIT_NAME,
        seed_index=0,
        beacon_client=client,
    )
    assert manifest.config["seed"] == manifest.beacon["derived_seed"]
    assert verify_precommit(manifest, run) == []


def test_verify_precommit_still_requires_verbatim_config_without_plan(tmp_path):
    run = _run_dir(tmp_path)
    pc = build_precommit(
        config={"trials": 3},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
    )
    write_precommit(pc, run / PRECOMMIT_NAME)
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="python run.py {run_dir}",
        config={"trials": 3, "seed": 0},
        precommit_path=run / PRECOMMIT_NAME,
    )
    problems = verify_precommit(manifest, run)
    assert any("config" in p for p in problems)


def test_binding_wrong_seed_and_wrong_round(tmp_path):
    client = _client()
    run = _run_dir(tmp_path)
    pc = build_precommit(
        config={"trials": 3},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=4,
        inclusion="all_in_campaign",
        beacon_client=client,
    )
    write_precommit(pc, run / PRECOMMIT_NAME)
    _attach_precommit_proof(run, pc)
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="python run.py {run_dir}",
        config={"trials": 3},
        precommit_path=run / PRECOMMIT_NAME,
        seed_index=2,
        beacon_client=client,
    )
    check = verify_beacon_binding(manifest, run, client=client)
    assert check.earned

    manifest.beacon = dict(manifest.beacon)
    manifest.beacon["derived_seed"] = 1
    manifest.config["seed"] = 1
    bad_seed = verify_beacon_binding(manifest, run, client=client)
    assert any("does not recompute" in p or "does not match derived" in p for p in bad_seed.problems)

    manifest.beacon["derived_seed"] = derive_seeds(pc["seed_plan"], {"trials": 3}, RANDOM_51)[2]
    manifest.config["seed"] = manifest.beacon["derived_seed"]
    manifest.beacon["round"] = 99
    bad_round = verify_beacon_binding(manifest, run, client=client)
    assert any("min_round" in p for p in bad_round.problems)


def test_unauthenticated_randomness_is_gap_not_problem(tmp_path):
    client = _client()
    run = _run_dir(tmp_path)
    pc = build_precommit(
        config={"trials": 3},
        command="go",
        git_sha="abc",
        git_dirty=False,
        seed_count=2,
        inclusion="all_in_campaign",
        beacon_client=client,
    )
    write_precommit(pc, run / PRECOMMIT_NAME)
    _attach_precommit_proof(run, pc)
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="go",
        config={"trials": 3},
        precommit_path=run / PRECOMMIT_NAME,
        seed_index=0,
        beacon_client=client,
    )
    missing = verify_beacon_binding(manifest, run, client=None)
    assert "unauthenticated randomness" in missing.gaps
    assert missing.problems == []

    empty = FixedBeacon(
        chain_hash=CHAIN, genesis_time=GENESIS, period=PERIOD, rounds={}, latest_round=0
    )
    unauth = verify_beacon_binding(manifest, run, client=empty)
    assert "unauthenticated randomness" in unauth.gaps
    assert unauth.problems == []


def test_evaluate_claims_earns_l2(tmp_path):
    client = _client()
    run = _run_dir(tmp_path)
    env = {"os": "Linux", "arch": "x86_64", "python": "3.12.0"}
    pc = build_precommit(
        config={"trials": 3},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=4,
        inclusion="all_in_campaign",
        beacon_client=client,
    )
    write_precommit(pc, run / PRECOMMIT_NAME)
    _attach_precommit_proof(run, pc)
    manifest = build_manifest(
        run,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="python run.py {run_dir}",
        environment=env,
        config={"trials": 3},
        precommit_path=run / PRECOMMIT_NAME,
        seed_index=2,
        beacon_client=client,
    )
    attach_bitcoin(manifest, run)
    card = evaluate_claims(manifest, run, beacon_client=client)
    assert card.ladder.level == "L2"
    assert card.ladder.next_level == "L3"
    assert card.ladder.next_meaning == L3_NEXT_MEANING
    assert "published 1 of 4 committed seeds" in "\n".join(card.notes)
    assert card.pre_specified == "precommit bound"


def test_evaluate_ladder_l2_gaps():
    card = SimpleNamespace(tamper_evident="pass", existed_by="Bitcoin height 1")
    manifest = SimpleNamespace(
        command="x", git_sha="abc", environment={"os": "L", "arch": "x", "python": "3"}
    )
    result = evaluate_ladder(card, manifest, beacon_gaps=["missing: seed_plan"])
    assert result.level == "L1"
    assert result.next_gaps == ["missing: seed_plan"]
    earned = evaluate_ladder(card, manifest, beacon_gaps=[])
    assert earned.level == "L2"
    assert earned.next_level == "L3"


def test_campaign_lists_missing_indices(tmp_path):
    client = _client()
    pc = build_precommit(
        config={"trials": 100},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
        seed_count=4,
        inclusion="all_in_campaign",
        beacon_client=client,
    )
    children = []
    for index in (1, 3):
        run = tmp_path / f"seed-{index}"
        run.mkdir()
        (run / "summary.csv").write_text(f"i,{index}\n", encoding="utf-8")
        write_precommit(pc, run / PRECOMMIT_NAME)
        _attach_precommit_proof(run, pc)
        manifest = build_manifest(
            run,
            publish_patterns=["*.csv"],
            git_sha="abc",
            git_dirty=False,
            command="python run.py {run_dir}",
            config={"trials": 100},
            precommit_path=run / PRECOMMIT_NAME,
            seed_index=index,
            beacon_client=client,
        )
        write_manifest(manifest, run)
        children.append(run)
    campaign = build_campaign(children, name="sweep", campaign_dir=tmp_path)
    assert campaign.seed_plan["count"] == 4
    note = campaign_seed_coverage_note(campaign)
    assert note is not None
    assert "published 2 of 4" in note
    assert "missing: 0, 2" in note


def test_subset_note_format():
    assert "missing: 0, 2" in subset_note(4, [1, 3])
    assert "missing members not on this record" in subset_note(
        8, [3], single_record=True
    )


def test_cli_precommit_derive_and_manifest(tmp_path, capsys):
    fixture = tmp_path / "beacon.json"
    write_fixed_beacon_fixture(
        fixture,
        chain_hash=CHAIN,
        genesis_time=GENESIS,
        period=PERIOD,
        latest=LATEST,
        rounds={LATEST: RANDOM_50, MIN_ROUND: RANDOM_51},
    )
    config = tmp_path / "config.json"
    config.write_text('{"trials": 2}', encoding="utf-8")
    assert (
        main(
            [
                "precommit",
                "--config",
                str(config),
                "--command",
                "python run.py {run_dir}",
                "--git-sha",
                "abc",
                "--out",
                str(tmp_path),
                "--allow-dirty",
                "--seed-count",
                "2",
                "--inclusion",
                "all_in_campaign",
                "--beacon-fixture",
                str(fixture),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "derive-seeds",
                "--precommit",
                str(tmp_path / PRECOMMIT_NAME),
                "--beacon-fixture",
                str(fixture),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "round 51" in out
    run = _run_dir(tmp_path)
    assert (
        main(
            [
                "manifest",
                "--run-dir",
                str(run),
                "--git-sha",
                "abc",
                "--command",
                "python run.py {run_dir}",
                "--config",
                str(config),
                "--publish",
                "*.csv",
                "--precommit",
                str(tmp_path / PRECOMMIT_NAME),
                "--seed-index",
                "0",
                "--seeds",
                str(tmp_path / "seeds.json"),
                "--beacon-fixture",
                str(fixture),
            ]
        )
        == 0
    )
    from farm_notary.manifest import load_manifest
    from farm_notary.precommit import load_precommit

    manifest = load_manifest(run)
    assert manifest.beacon["seed_index"] == 0
    assert manifest.beacon["round"] == MIN_ROUND
    plan = load_precommit(tmp_path / PRECOMMIT_NAME)["seed_plan"]
    assert manifest.config["seed"] == derive_seeds(plan, {"trials": 2}, RANDOM_51)[0]


def test_drand_http_client_uses_opener():
    info = (
        b'{"hash":"abc","genesis_time":1000,"period":3}'
    )
    latest = b'{"round":10,"randomness":"' + b"cc" * 32 + b'"}'

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = []

    def opener(url, timeout=10.0):
        calls.append(url)
        if url.endswith("/info"):
            return _Resp(info)
        if url.endswith("/public/latest"):
            return _Resp(latest)
        if url.endswith("/public/10"):
            return _Resp(latest)
        raise URLError("unexpected")

    client = DrandHttpClient(
        base_url="https://example.test", chain_hash="abc", opener=opener
    )
    fetched = client.latest()
    assert fetched.round == 10
    assert fetched.randomness == bytes.fromhex("cc" * 32)
    assert client.get_round(10).round == 10
    assert any("/info" in u for u in calls)


def test_drand_http_client_wraps_errors():
    def opener(url, timeout=10.0):
        raise URLError("down")

    client = DrandHttpClient(opener=opener)
    with pytest.raises(BeaconError, match="drand fetch failed"):
        client.chain_info()


def test_config_hash_used_in_derivation():
    a = config_hash_excluding_seed({"trials": 1, "seed": 0})
    b = config_hash_excluding_seed({"trials": 1, "seed": 9})
    assert a == b
    s0 = derive_seed_v1(
        chain_hash=CHAIN, round=1, index=0, config_hash=a, randomness=RANDOM_51
    )
    s1 = derive_seed_v1(
        chain_hash=CHAIN, round=1, index=0, config_hash=b, randomness=RANDOM_51
    )
    assert s0 == s1
