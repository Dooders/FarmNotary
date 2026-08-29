"""Tests for Sigstore keyless signing of reproduction receipts.

Covers:
- receipt_signable_bytes stability and sigstore-field exclusion (no cosign needed)
- L3 ladder logic via mocked sigstore verification
- verify_receipt with mocked cosign (good and bad bundle)
- CLI --sign flag with mocked cosign
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from farm_notary.ladder import L3_GAP_SIGNATURE, LADDER_MEANINGS, evaluate_ladder
from farm_notary.reproduce import build_receipt
from farm_notary.sigstore import receipt_signable_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _l2_card():
    return SimpleNamespace(tamper_evident="pass", existed_by="Bitcoin height 800000")


def _l2_manifest():
    return SimpleNamespace(
        command="python run.py {run_dir}",
        environment={"os": "Linux", "arch": "x86_64", "python": "3.12.0"},
        git_sha="abc",
    )


def _make_receipt(**overrides):
    base = {
        "schema": "farmnotary.reproduction.v1",
        "created_utc": "2025-01-01T00:00:00Z",
        "original_manifest_hash": "ab" * 32,
        "command": "python run.py {run_dir}",
        "environment": {"os": "Linux"},
        "ok": True,
        "returncode": 0,
        "matched": ["summary.csv"],
        "mismatched": [],
        "missing": [],
        "ignored": [],
        "ignore": [],
        "diagnostics": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# receipt_signable_bytes
# ---------------------------------------------------------------------------

def test_signable_bytes_excludes_sigstore_field():
    receipt = _make_receipt(sigstore={"bundle": "data"})
    byt = receipt_signable_bytes(receipt)
    parsed = json.loads(byt)
    assert "sigstore" not in parsed
    # Fields without sigstore are present
    assert parsed["ok"] is True


def test_signable_bytes_stable_across_roundtrip():
    """Adding then removing sigstore yields the same bytes as never adding it."""
    receipt_plain = _make_receipt()
    receipt_with = dict(receipt_plain)
    receipt_with["sigstore"] = {"bundle": "anything"}

    byt_plain = receipt_signable_bytes(receipt_plain)
    byt_with = receipt_signable_bytes(receipt_with)
    assert byt_plain == byt_with


def test_receipt_hash_excludes_sigstore():
    from farm_notary.reproduce import receipt_hash

    r = _make_receipt()
    h1 = receipt_hash(r)
    r2 = dict(r)
    r2["sigstore"] = {"bundle": "x"}
    h2 = receipt_hash(r2)
    assert h1 == h2


# ---------------------------------------------------------------------------
# L3 ladder logic
# ---------------------------------------------------------------------------

def test_l2_without_sigstore_stays_l2():
    result = evaluate_ladder(_l2_card(), _l2_manifest(), beacon_gaps=[], receipt_sigstore=False)
    assert result.level == "L2"
    assert result.next_level == "L3"
    assert L3_GAP_SIGNATURE in result.next_gaps


def test_l2_with_sigstore_earns_l3():
    result = evaluate_ladder(_l2_card(), _l2_manifest(), beacon_gaps=[], receipt_sigstore=True)
    assert result.level == "L3"
    assert result.meaning == LADDER_MEANINGS["L3"]
    assert result.next_level == ""


def test_l3_lines_no_next():
    result = evaluate_ladder(_l2_card(), _l2_manifest(), beacon_gaps=[], receipt_sigstore=True)
    rendered = "\n".join(result.lines())
    assert "level: L3" in rendered
    assert "next:" not in rendered


def test_l1_with_sigstore_does_not_skip_to_l3():
    """Sigstore only helps once L2 is earned; it does not bypass the beacon."""
    result = evaluate_ladder(_l2_card(), _l2_manifest(), beacon_gaps=None, receipt_sigstore=True)
    # Without beacon, L2 is not earned so L3 cannot be earned.
    assert result.level == "L1"


def test_unsigned_receipt_cannot_earn_l3():
    result = evaluate_ladder(_l2_card(), _l2_manifest(), beacon_gaps=[], receipt_sigstore=False)
    assert result.level == "L2"
    assert "L3" not in result.level


# ---------------------------------------------------------------------------
# verify_receipt with mocked cosign
# ---------------------------------------------------------------------------

def _write_receipt_with_bundle(tmp_path: Path, receipt: dict, bundle: dict):
    from farm_notary.manifest import RECEIPT_NAME

    receipt["sigstore"] = bundle
    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


def _make_manifest_for_receipt(tmp_path: Path, receipt: dict):
    from farm_notary.manifest import build_manifest, write_manifest

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    m = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    # Override hash to match receipt
    m._content_hash = receipt["original_manifest_hash"]
    return m


def test_verify_receipt_no_sigstore_still_passes(tmp_path: Path):
    """Unsigned receipts verify as self-attested (no cosign required)."""
    from farm_notary.manifest import RECEIPT_NAME, build_manifest, write_manifest
    from farm_notary.verify import verify_receipt

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)

    receipt = _make_receipt(original_manifest_hash=manifest.content_hash())
    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )

    problems = verify_receipt(manifest, tmp_path)
    assert problems == []


def test_verify_receipt_valid_sigstore_bundle(tmp_path: Path):
    """A valid sigstore bundle (mocked cosign) adds no problems."""
    from farm_notary.manifest import RECEIPT_NAME, build_manifest, write_manifest
    from farm_notary.verify import verify_receipt

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)

    receipt = _make_receipt(original_manifest_hash=manifest.content_hash())
    bundle = {"verificationMaterial": {}, "messageSignature": {}}
    receipt["sigstore"] = bundle
    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="Verified OK", stderr="")
        problems = verify_receipt(manifest, tmp_path)

    assert problems == []


def test_verify_receipt_bad_sigstore_bundle(tmp_path: Path):
    """A failing cosign verify-blob is reported as a problem."""
    from farm_notary.manifest import RECEIPT_NAME, build_manifest, write_manifest
    from farm_notary.verify import verify_receipt

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)

    receipt = _make_receipt(original_manifest_hash=manifest.content_hash())
    bundle = {"verificationMaterial": {}, "messageSignature": {}}
    receipt["sigstore"] = bundle
    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error: signature expired"
        )
        problems = verify_receipt(manifest, tmp_path)

    assert any("sigstore" in p for p in problems)
    assert any("signature expired" in p for p in problems)


def test_verify_receipt_cosign_not_available_no_hard_failure(tmp_path: Path):
    """If cosign is not installed, a signed receipt does not block verify."""
    from farm_notary.manifest import RECEIPT_NAME, build_manifest, write_manifest
    from farm_notary.verify import verify_receipt

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)

    receipt = _make_receipt(original_manifest_hash=manifest.content_hash())
    receipt["sigstore"] = {"bundle": "data"}
    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )

    # cosign not on PATH → cosign_available() returns False
    with patch("farm_notary.sigstore.shutil.which", return_value=None):
        problems = verify_receipt(manifest, tmp_path)

    # No sigstore-related problem: unsigned path
    assert not any("sigstore" in p for p in problems)


# ---------------------------------------------------------------------------
# evaluate_claims L3 integration (mocked cosign)
# ---------------------------------------------------------------------------

def test_evaluate_claims_signed_receipt_earns_l3(tmp_path: Path):
    """evaluate_claims awards L3 when beacon passes and cosign verifies."""
    from farm_notary.anchor import anchor_run
    from farm_notary.beacon import BeaconCheck
    from farm_notary.manifest import RECEIPT_NAME, build_manifest, write_manifest
    from farm_notary.ots import PROOF_NAME, serialize_proof
    from farm_notary.reproduce import ReproductionResult, build_receipt, write_receipt
    from farm_notary.verify import evaluate_claims
    from tests.test_ots import bitcoin_timestamp

    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="abc",
        git_dirty=False,
        command="python run.py {run_dir}",
    )
    write_manifest(manifest, tmp_path)
    anchor_run(manifest)
    manifest.anchor["backend"] = "opentimestamps"
    manifest.anchor["detail"] = {"proof": PROOF_NAME}
    digest = bytes.fromhex(manifest.content_hash())
    (tmp_path / PROOF_NAME).write_bytes(serialize_proof(bitcoin_timestamp(digest, 800000)))

    result = ReproductionResult(
        command="python run.py {run_dir}",
        returncode=0,
        matched=["summary.csv"],
    )
    receipt = build_receipt(manifest, result)
    receipt["sigstore"] = {"bundle": "mock"}
    write_receipt(receipt, tmp_path)

    # Mock beacon to pass (no gaps) so L2 is earned, then sigstore earns L3.
    empty_beacon = BeaconCheck(gaps=[], problems=[], notes=[])

    with (
        patch("farm_notary.verify.verify_beacon_binding", return_value=empty_beacon),
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        card = evaluate_claims(manifest, tmp_path)

    assert card.ladder.level == "L3"
    assert LADDER_MEANINGS["L3"] in card.render()


# ---------------------------------------------------------------------------
# CLI --sign flag (mocked cosign)
# ---------------------------------------------------------------------------

def test_cli_reproduce_sign_flag(tmp_path: Path, capsys):
    """--sign embeds the Sigstore bundle in the receipt."""
    from farm_notary.cli import main
    from farm_notary.manifest import RECEIPT_NAME
    from tests.test_reproduce import make_notarized_run

    run_dir, manifest, _ = make_notarized_run(tmp_path)
    fake_bundle = {"mediaType": "test", "messageSignature": {}}

    with patch("farm_notary.sigstore.sign_receipt", return_value=fake_bundle):
        ret = main(["reproduce", "--run-dir", str(run_dir), "--sign"])

    assert ret == 0
    out = capsys.readouterr().out
    assert "receipt signed with Sigstore keyless" in out

    receipt_data = json.loads((run_dir / RECEIPT_NAME).read_text(encoding="utf-8"))
    assert "sigstore" in receipt_data
    assert receipt_data["sigstore"]["mediaType"] == "test"
