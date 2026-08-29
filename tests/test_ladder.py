from types import SimpleNamespace

from farm_notary.anchor import anchor_run
from farm_notary.ladder import L0_MEANING, L2_NEXT_MEANING, LADDER_NONE, evaluate_ladder
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.ots import PROOF_NAME, serialize_proof
from farm_notary.reproduce import ReproductionResult, build_receipt, write_receipt
from farm_notary.verify import evaluate_claims
from tests.test_ots import bitcoin_timestamp, pending_timestamp


def _card(**kwargs):
    defaults = {
        "tamper_evident": "pass",
        "existed_by": "missing",
        "pre_specified": "missing",
        "bitwise_reproducible": "missing",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _manifest(command=None, environment=None, git_sha="abc"):
    return SimpleNamespace(
        command=command, environment=environment or {}, git_sha=git_sha
    )


def _fingerprint_env():
    return {"os": "Linux", "arch": "x86_64", "python": "3.12.0"}


def _l1_manifest(**kwargs):
    defaults = {
        "command": "python run.py {run_dir}",
        "environment": _fingerprint_env(),
        "git_sha": "abc",
    }
    defaults.update(kwargs)
    return _manifest(**defaults)


def make_run(tmp_path, *, command=None, git_sha="abc"):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha=git_sha,
        git_dirty=False,
        command=command,
    )


def attach_bitcoin(manifest, run_dir, height=800000):
    write_manifest(manifest, run_dir)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    digest = bytes.fromhex(manifest.content_hash())
    (run_dir / PROOF_NAME).write_bytes(serialize_proof(bitcoin_timestamp(digest, height)))


def attach_pending(manifest, run_dir):
    write_manifest(manifest, run_dir)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    digest = bytes.fromhex(manifest.content_hash())
    (run_dir / PROOF_NAME).write_bytes(
        serialize_proof(pending_timestamp(digest, "https://example.com"))
    )


def test_bare_rehash_is_none_next_l0():
    card = _card()
    result = evaluate_ladder(card, _manifest())
    assert result.level == LADDER_NONE
    assert result.next_level == "L0"
    assert result.next_meaning == L0_MEANING
    assert result.next_gaps == ["missing: Bitcoin attestation"]


def test_pending_ots_is_not_l0():
    card = _card(existed_by="pending")
    result = evaluate_ladder(card, _l1_manifest())
    assert result.level == LADDER_NONE
    assert "Bitcoin attestation" in result.next_gaps[0]


def test_bitcoin_without_command_is_l0():
    card = _card(existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest(command=None))
    assert result.level == "L0"
    assert result.meaning == L0_MEANING
    assert result.next_level == "L1"
    assert result.next_gaps == ["missing: command"]


def test_bitcoin_without_environment_is_l0():
    card = _card(existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest(environment={}))
    assert result.level == "L0"
    assert result.next_gaps == ["missing: environment fingerprint"]


def test_bitcoin_without_git_sha_is_l0():
    card = _card(existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest(git_sha=None))
    assert result.level == "L0"
    assert result.next_gaps == ["missing: git SHA"]


def test_whitespace_command_is_not_l1():
    card = _card(existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest(command="  \t"))
    assert result.level == "L0"
    assert result.next_gaps == ["missing: command"]


def test_bitcoin_command_fingerprint_and_sha_is_l1():
    card = _card(existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest())
    assert result.level == "L1"
    assert "command was not run" in result.meaning
    assert result.next_level == "L2"
    assert result.next_meaning == L2_NEXT_MEANING
    assert result.next_gaps == ["missing: seed_plan"]


def test_tamper_fail_is_none_even_with_bitcoin():
    card = _card(tamper_evident="fail", existed_by="Bitcoin height 800000")
    result = evaluate_ladder(card, _l1_manifest())
    assert result.level == LADDER_NONE
    assert result.next_gaps == ["tamper-evident failed"]


def test_existed_by_fail_is_none():
    card = _card(existed_by="fail")
    result = evaluate_ladder(card, _l1_manifest())
    assert result.level == LADDER_NONE
    assert result.next_gaps == ["missing: Bitcoin attestation"]


def test_evaluate_claims_bare_is_none(tmp_path):
    manifest = make_run(tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ladder.level == LADDER_NONE
    rendered = card.render()
    assert "level: none" in rendered
    assert "next:  L0" in rendered
    assert L0_MEANING in rendered
    assert "missing: Bitcoin attestation" in rendered
    assert "independently reproduced" not in rendered


def test_evaluate_claims_pending_is_not_l0(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    attach_pending(manifest, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.existed_by == "pending"
    assert card.ladder.level == LADDER_NONE


def test_evaluate_claims_eas_is_not_l0(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    write_manifest(manifest, tmp_path)
    manifest.anchor = {
        "backend": "eas",
        "manifest_hash": manifest.content_hash(),
        "attestation_uid": "0xabc",
    }
    card = evaluate_claims(manifest, tmp_path)
    assert card.existed_by == "missing"
    assert card.ladder.level == LADDER_NONE
    assert card.ladder.next_gaps == ["missing: Bitcoin attestation"]


def test_evaluate_claims_proof_fail_is_none(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    write_manifest(manifest, tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    (tmp_path / PROOF_NAME).write_bytes(serialize_proof(bitcoin_timestamp(b"\xff" * 32, 800000)))
    card = evaluate_claims(manifest, tmp_path)
    assert card.existed_by == "fail"
    assert card.ladder.level == LADDER_NONE
    assert card.ladder.next_gaps == ["missing: Bitcoin attestation"]


def test_evaluate_claims_bitcoin_without_command_is_l0(tmp_path):
    manifest = make_run(tmp_path)
    attach_bitcoin(manifest, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ladder.level == "L0"
    assert "missing: command" in card.ladder.next_gaps
    assert "level: L0" in card.render()
    assert L0_MEANING in card.render()


def test_evaluate_claims_bitcoin_with_command_is_l1(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    attach_bitcoin(manifest, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ladder.level == "L1"
    assert card.ladder.next_level == "L2"
    assert L2_NEXT_MEANING in card.render()
    assert "missing: seed_plan" in card.render()
    assert "command was not run" in card.render()


def test_unsigned_receipt_does_not_earn_l3(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    attach_bitcoin(manifest, tmp_path)
    result = ReproductionResult(
        command="python run.py {run_dir}",
        returncode=0,
        matched=["summary.csv"],
    )
    write_receipt(build_receipt(manifest, result), tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ladder.level == "L1"
    assert card.ladder.next_level == "L2"
    rendered = card.render()
    assert "independently reproduced" not in rendered
    assert "L3" not in rendered.split("level:", 1)[1].splitlines()[0]


def test_tamper_with_bitcoin_proof_is_none(tmp_path):
    manifest = make_run(tmp_path, command="python run.py {run_dir}")
    attach_bitcoin(manifest, tmp_path)
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.9\n", encoding="utf-8")
    card = evaluate_claims(manifest, tmp_path)
    assert card.tamper_evident == "fail"
    assert card.ladder.level == LADDER_NONE
    assert card.ladder.next_gaps == ["tamper-evident failed"]
