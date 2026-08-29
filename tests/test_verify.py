from pathlib import Path

from farm_notary.anchor import anchor_run
from farm_notary.manifest import build_manifest, write_manifest
from farm_notary.ots import PROOF_NAME, serialize_proof
from farm_notary.precommit import PRECOMMIT_NAME, build_precommit, write_precommit
from farm_notary.reproduce import build_receipt, write_receipt
from farm_notary.verify import evaluate_claims, verify_anchor, verify_run_dir
from tests.test_ots import bitcoin_timestamp, pending_timestamp


def make_manifest(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )


def write_proof_for(manifest, run_dir: Path, digest_hex=None) -> Path:
    digest = bytes.fromhex(digest_hex or manifest.content_hash())
    path = run_dir / PROOF_NAME
    path.write_bytes(serialize_proof(pending_timestamp(digest, "https://example.com")))
    return path


def test_verify_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    assert verify_run_dir(manifest, tmp_path) == []


def test_verify_detects_tampering(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.9\n", encoding="utf-8")
    assert verify_run_dir(manifest, tmp_path) == ["artifact hash mismatch: summary.csv"]


def test_verify_detects_missing_artifact(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").unlink()
    problems = verify_run_dir(manifest, tmp_path)
    assert len(problems) == 1
    assert "artifact unreachable" in problems[0]
    assert "summary.csv" in problems[0]


def test_verify_detects_missing_artifact_with_cid_hint(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.cid = "bafytest123"
    (tmp_path / "summary.csv").unlink()
    problems = verify_run_dir(manifest, tmp_path)
    assert len(problems) == 1
    assert "artifact unreachable" in problems[0]
    assert "bafytest123" in problems[0]


def test_verify_flags_invalid_manifest(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    manifest.artifacts.append("ghost.csv")
    problems = verify_run_dir(manifest, tmp_path)
    assert any("invalid manifest" in p for p in problems)


def test_verify_anchor_skips_unanchored_manifest(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    assert manifest.anchor is None
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_dry_run_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_detects_manifest_body_edit(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.runner = "edited-after-anchoring"
    problems = verify_anchor(manifest, tmp_path)
    assert len(problems) == 1
    assert "anchored hash" in problems[0]


def test_verify_anchor_ots_proof_ok(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    write_proof_for(manifest, tmp_path)
    assert verify_anchor(manifest, tmp_path) == []


def test_verify_anchor_ots_missing_proof(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    problems = verify_anchor(manifest, tmp_path)
    assert problems == [f"missing anchor proof: {PROOF_NAME}"]


def test_verify_anchor_ots_proof_digest_mismatch(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    write_proof_for(manifest, tmp_path, digest_hex="ff" * 32)
    problems = verify_anchor(manifest, tmp_path)
    assert len(problems) == 1
    assert "proof commits to" in problems[0]


def _card_line(rendered: str, claim: str) -> str:
    for line in rendered.splitlines():
        if line.startswith("•") and claim in line:
            return line
    raise AssertionError(f"claim {claim!r} not in card:\n{rendered}")


def test_claim_card_bare_manifest_is_honest_about_missing(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.tamper_evident == "pass"
    assert card.existed_by == "missing"
    assert card.pre_specified == "missing"
    assert card.bitwise_reproducible == "missing"
    rendered = card.render()
    assert rendered.startswith("claim card\n")
    assert "level: none" in rendered
    assert "next:  L0" in rendered
    assert "missing: Bitcoin attestation" in rendered
    assert "not claimed: scientific correctness" in rendered
    assert "•  tamper-evident record" in rendered
    assert "— pass" in _card_line(rendered, "tamper-evident record")
    assert "— missing" in _card_line(rendered, "existed by time T")
    assert "— missing" in _card_line(rendered, "pre-specified design")
    assert "— missing" in _card_line(rendered, "bitwise reproducible")


def test_claim_card_tamper_fails_only_that_claim(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.9\n", encoding="utf-8")
    card = evaluate_claims(manifest, tmp_path)
    assert not card.ok
    assert card.tamper_evident == "fail"
    assert card.existed_by == "missing"
    assert card.pre_specified == "missing"
    assert card.bitwise_reproducible == "missing"
    assert any("hash mismatch" in p for p in card.problems)


def test_claim_card_existed_by_pending_and_bitcoin_height(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    write_proof_for(manifest, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.existed_by == "pending"

    digest = bytes.fromhex(manifest.content_hash())
    (tmp_path / PROOF_NAME).write_bytes(serialize_proof(bitcoin_timestamp(digest, 800000)))
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.existed_by == "Bitcoin height 800000"


def test_claim_card_existed_by_fail_on_digest_mismatch(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    write_proof_for(manifest, tmp_path, digest_hex="ff" * 32)
    card = evaluate_claims(manifest, tmp_path)
    assert not card.ok
    assert card.existed_by == "fail"


def test_claim_card_precommit_bound_or_fail(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    pc = build_precommit(
        config={"trials": 1},
        command="python run.py {run_dir}",
        git_sha="abc",
        git_dirty=False,
    )
    write_precommit(pc, tmp_path / PRECOMMIT_NAME)
    manifest = build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        command="python run.py {run_dir}",
        config={"trials": 1},
        precommit_path=tmp_path / PRECOMMIT_NAME,
    )
    write_manifest(manifest, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.pre_specified == "precommit bound"

    pc["command"] = "python EVIL.py {run_dir}"
    write_precommit(pc, tmp_path / PRECOMMIT_NAME)
    card = evaluate_claims(manifest, tmp_path)
    assert not card.ok
    assert card.pre_specified == "fail"


def test_claim_card_bitwise_scoped_with_ignore_globs(tmp_path: Path):
    from farm_notary.reproduce import ReproductionResult

    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    result = ReproductionResult(
        command="python run.py",
        returncode=0,
        matched=["summary.csv"],
        ignored=["media.mp4"],
        ignore=["*.mp4"],
    )
    receipt = build_receipt(manifest, result)
    write_receipt(receipt, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    from farm_notary.scope import format_bitwise_status

    assert card.bitwise_reproducible == format_bitwise_status(
        "1/1, ignored: *.mp4", receipt["environment"], ok=True
    )


def test_claim_card_bitwise_old_receipt_lists_ignored_files(tmp_path: Path):
    """Receipts written before the ignore-glob field still list excluded files."""
    from farm_notary.reproduce import ReproductionResult

    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    result = ReproductionResult(
        command="python run.py",
        returncode=0,
        matched=["summary.csv"],
        ignored=["media.mp4"],
        ignore=["*.mp4"],
    )
    receipt = build_receipt(manifest, result)
    del receipt["ignore"]
    write_receipt(receipt, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    from farm_notary.scope import format_bitwise_status

    assert card.bitwise_reproducible == format_bitwise_status(
        "1/1, ignored: media.mp4", receipt["environment"], ok=True
    )


def test_claim_card_arm_receipt_refuses_cross_hardware_claim(tmp_path: Path):
    from farm_notary.reproduce import ReproductionResult
    from farm_notary.scope import ALLOWED_SENTENCE, CROSS_HARDWARE_NOT_A_CLAIM

    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    result = ReproductionResult(
        command="python run.py",
        returncode=0,
        matched=["summary.csv"],
    )
    receipt = build_receipt(manifest, result)
    receipt["environment"] = {
        "python": "3.12.0",
        "system": "Darwin",
        "machine": "arm64",
        "platform": "macOS-14.6-arm64-arm-64bit",
    }
    write_receipt(receipt, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.bitwise_reproducible == (
        f"1/1 on ARM64 macOS; {CROSS_HARDWARE_NOT_A_CLAIM}"
    )
    assert ALLOWED_SENTENCE not in card.bitwise_reproducible


def test_claim_card_bitwise_zero_compared_is_missing(tmp_path: Path):
    """A receipt that compared nothing has not earned a bitwise claim."""
    from farm_notary.reproduce import ReproductionResult

    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    result = ReproductionResult(command="python run.py", returncode=0)
    receipt = build_receipt(manifest, result)
    write_receipt(receipt, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.bitwise_reproducible == "missing"


def test_claim_card_bitwise_fail_shows_score(tmp_path: Path):
    from farm_notary.reproduce import ReproductionResult

    manifest = make_manifest(tmp_path)
    write_manifest(manifest, tmp_path)
    result = ReproductionResult(
        command="python run.py",
        returncode=0,
        matched=["summary.csv"],
        mismatched=["REPORT.md"],
    )
    receipt = build_receipt(manifest, result)
    receipt["ok"] = False
    write_receipt(receipt, tmp_path)
    card = evaluate_claims(manifest, tmp_path)
    assert not card.ok
    assert card.bitwise_reproducible == "fail — 1/2"


def test_claim_card_dry_run_anchor_does_not_earn_existed_by(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    anchor_run(manifest)
    card = evaluate_claims(manifest, tmp_path)
    assert card.ok
    assert card.existed_by == "missing"
