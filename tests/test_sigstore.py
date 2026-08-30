"""Tests for Sigstore keyless signing of reproduction receipts.

Covers:
- receipt_signable_bytes stability, sigstore-field exclusion, and sort_keys canonicality
- L3 ladder logic via mocked sigstore verification
- verify_receipt with mocked cosign (good and bad bundle)
- CLI --sign flag with mocked cosign
- sign_receipt command construction (identity-token, missing cosign, nonzero exit, bad JSON)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from farm_notary.ladder import L3_GAP_SIGNATURE, LADDER_MEANINGS, evaluate_ladder
from farm_notary.reproduce import build_receipt, receipt_hash
from farm_notary.sigstore import (
    SIGSTORE_ID_TOKEN_ENV,
    SigstoreError,
    bundle_has_inclusion_proof,
    extract_bundle_identity,
    read_identity_token_cli,
    receipt_signable_bytes,
)


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


def test_signable_bytes_sort_keys():
    """Keys in signable bytes are sorted (canonical form independent of insertion order)."""
    receipt_a = {"z_field": 1, "a_field": 2}
    receipt_b = {"a_field": 2, "z_field": 1}
    assert receipt_signable_bytes(receipt_a) == receipt_signable_bytes(receipt_b)


def test_receipt_hash_excludes_sigstore():
    r = _make_receipt()
    h1 = receipt_hash(r)
    r2 = dict(r)
    r2["sigstore"] = {"bundle": "x"}
    h2 = receipt_hash(r2)
    assert h1 == h2


def test_signable_bytes_match_hash_json_canonicalization():
    """OTS and Sigstore commit to the same encoding."""
    import hashlib

    receipt = _make_receipt()
    expected = json.dumps(
        {k: v for k, v in receipt.items() if k != "sigstore"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert receipt_signable_bytes(receipt) == expected
    assert hashlib.sha256(receipt_signable_bytes(receipt)).hexdigest() == receipt_hash(
        receipt
    )


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
        cmd = mock_run.call_args[0][0]

    assert problems == []
    assert "--offline" not in cmd


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
    assert "independent identity reproduced it" not in card.render()
    assert "independently reproduced" not in card.render()
    assert "sigstore identity could not be parsed" in card.notes


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

    with patch("farm_notary.cli.sign_receipt", return_value=fake_bundle):
        ret = main(["reproduce", "--run-dir", str(run_dir), "--sign"])

    assert ret == 0
    out = capsys.readouterr().out
    assert "receipt signed with Sigstore keyless" in out

    receipt_data = json.loads((run_dir / RECEIPT_NAME).read_text(encoding="utf-8"))
    assert "sigstore" in receipt_data
    assert receipt_data["sigstore"]["mediaType"] == "test"


# ---------------------------------------------------------------------------
# sign_receipt command construction
# ---------------------------------------------------------------------------

def _fake_bundle_file(bundle_path, bundle):
    """Side-effect helper: write bundle JSON to bundle_path when subprocess is called."""
    import json as _json

    def _side_effect(cmd, **kwargs):
        # locate --bundle argument and write the fake bundle there
        try:
            idx = cmd.index("--bundle")
            Path(cmd[idx + 1]).write_text(_json.dumps(bundle), encoding="utf-8")
        except (ValueError, IndexError):
            pass
        return MagicMock(returncode=0, stdout="", stderr="")

    return _side_effect


def test_sign_receipt_basic_command_construction():
    """sign_receipt calls cosign sign-blob with --bundle and --yes."""
    from farm_notary.sigstore import sign_receipt

    fake_bundle = {"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3"}
    receipt = _make_receipt()

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = _fake_bundle_file(None, fake_bundle)
        result = sign_receipt(receipt)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/bin/cosign"
    assert "sign-blob" in cmd
    assert "--bundle" in cmd
    assert "--yes" in cmd
    assert result == fake_bundle


def test_sign_receipt_includes_identity_token():
    """Token is passed via SIGSTORE_ID_TOKEN, never --identity-token on argv."""
    from farm_notary.sigstore import sign_receipt

    fake_bundle = {"mediaType": "test"}
    receipt = _make_receipt()

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = _fake_bundle_file(None, fake_bundle)
        sign_receipt(receipt, identity_token="MY_TOKEN")

    cmd = mock_run.call_args[0][0]
    assert "--identity-token" not in cmd
    env = mock_run.call_args.kwargs["env"]
    assert env[SIGSTORE_ID_TOKEN_ENV] == "MY_TOKEN"


def test_sign_receipt_missing_cosign_raises():
    """sign_receipt raises SigstoreError when cosign is not on PATH."""
    from farm_notary.sigstore import SigstoreError, sign_receipt

    with patch("farm_notary.sigstore.shutil.which", return_value=None):
        with pytest.raises(SigstoreError, match="cosign is not on PATH"):
            sign_receipt(_make_receipt())


def test_sign_receipt_nonzero_exit_raises():
    """sign_receipt raises SigstoreError when cosign exits non-zero."""
    from farm_notary.sigstore import SigstoreError, sign_receipt

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch(
            "farm_notary.sigstore.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="OIDC error"),
        ),
    ):
        with pytest.raises(SigstoreError, match="OIDC error"):
            sign_receipt(_make_receipt())


def test_sign_receipt_malformed_bundle_json_raises():
    """sign_receipt raises SigstoreError when cosign writes invalid JSON."""
    from farm_notary.sigstore import SigstoreError, sign_receipt

    def _write_bad_json(cmd, **kwargs):
        try:
            idx = cmd.index("--bundle")
            Path(cmd[idx + 1]).write_text("not-valid-json{{{", encoding="utf-8")
        except (ValueError, IndexError):
            pass
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run", side_effect=_write_bad_json),
    ):
        with pytest.raises(SigstoreError, match="malformed bundle JSON"):
            sign_receipt(_make_receipt())


def test_sign_receipt_bundle_not_object_raises():
    """sign_receipt raises SigstoreError when the bundle JSON is not a dict."""
    from farm_notary.sigstore import SigstoreError, sign_receipt

    def _write_array(cmd, **kwargs):
        try:
            idx = cmd.index("--bundle")
            Path(cmd[idx + 1]).write_text("[1, 2, 3]", encoding="utf-8")
        except (ValueError, IndexError):
            pass
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run", side_effect=_write_array),
    ):
        with pytest.raises(SigstoreError, match="not a JSON object"):
            sign_receipt(_make_receipt())


def test_read_identity_token_cli_rejects_raw_jwt():
    with pytest.raises(SigstoreError, match="@PATH"):
        read_identity_token_cli("eyJhbGciOi.e30.sig")


def test_read_identity_token_cli_reads_at_path(tmp_path: Path):
    token_path = tmp_path / "oidc.txt"
    token_path.write_text("file-token\n", encoding="utf-8")
    assert read_identity_token_cli(f"@{token_path}") == "file-token"


def test_bundle_has_inclusion_proof():
    # Entry with a populated inclusionProof → True
    assert bundle_has_inclusion_proof(
        {"verificationMaterial": {"tlogEntries": [{"inclusionProof": {"checkpoint": "x"}}]}}
    )
    # Legacy rekorBundle format → True
    assert bundle_has_inclusion_proof({"rekorBundle": {"signedEntryTimestamp": "abc"}})
    # Empty tlogEntry (no inclusionProof) → False
    assert not bundle_has_inclusion_proof({"verificationMaterial": {"tlogEntries": [{}]}})
    # tlogEntry with logIndex but no inclusionProof → False
    assert not bundle_has_inclusion_proof(
        {"verificationMaterial": {"tlogEntries": [{"logIndex": "1"}]}}
    )
    assert not bundle_has_inclusion_proof({"verificationMaterial": {}})
    assert not bundle_has_inclusion_proof("not-a-dict")


def test_verify_uses_offline_when_bundle_has_tlog(tmp_path: Path):
    from farm_notary.manifest import build_manifest, write_manifest
    from farm_notary.verify import verify_receipt

    (tmp_path / "summary.csv").write_text("ok\n", encoding="utf-8")
    manifest = build_manifest(tmp_path, publish_patterns=["*.csv"], git_sha="abc")
    write_manifest(manifest, tmp_path)
    receipt = _make_receipt(original_manifest_hash=manifest.content_hash())
    receipt["sigstore"] = {
        "verificationMaterial": {
            "tlogEntries": [{"logIndex": "1", "inclusionProof": {"checkpoint": "x"}}]
        }
    }
    from farm_notary.manifest import RECEIPT_NAME

    (tmp_path / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    with (
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert verify_receipt(manifest, tmp_path) == []
        assert "--offline" in mock_run.call_args[0][0]


def test_verify_receipt_notes_missing_cosign(tmp_path: Path):
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
    notes: list[str] = []
    with patch("farm_notary.sigstore.shutil.which", return_value=None):
        problems = verify_receipt(manifest, tmp_path, notes=notes)
    assert problems == []
    assert any("cosign not on PATH" in n for n in notes)


def test_extract_identity_v2_issuer():
    import base64
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer_url = b"https://token.actions.githubusercontent.com"
    der_issuer = b"\x0c" + bytes([len(issuer_url)]) + issuer_url
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")]))
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name("lab@example.com")]),
            critical=False,
        )
        .add_extension(
            x509.UnrecognizedExtension(
                x509.ObjectIdentifier("1.3.6.1.4.1.57264.1.8"), der_issuer
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    raw = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")
    bundle = {
        "verificationMaterial": {
            "x509CertificateChain": {"certificates": [{"rawBytes": raw}]}
        }
    }
    identity = extract_bundle_identity(bundle)
    assert identity["subject"] == "lab@example.com"
    assert identity["issuer"] == "https://token.actions.githubusercontent.com"


def test_failed_receipt_with_bundle_does_not_earn_l3(tmp_path: Path):
    from farm_notary.anchor import anchor_run
    from farm_notary.beacon import BeaconCheck
    from farm_notary.manifest import build_manifest, write_manifest
    from farm_notary.ots import PROOF_NAME, serialize_proof
    from farm_notary.reproduce import ReproductionResult, write_receipt
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
        returncode=1,
        matched=[],
        mismatched=["summary.csv"],
    )
    receipt = build_receipt(manifest, result)
    receipt["sigstore"] = {"verificationMaterial": {"tlogEntries": [{}]}}
    write_receipt(receipt, tmp_path)
    empty_beacon = BeaconCheck(gaps=[], problems=[], notes=[])
    with (
        patch("farm_notary.verify.verify_beacon_binding", return_value=empty_beacon),
        patch("farm_notary.sigstore.shutil.which", return_value="/usr/bin/cosign"),
        patch("farm_notary.sigstore.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        card = evaluate_claims(manifest, tmp_path)
    assert card.ladder.level == "L2"
    assert card.ladder.level != "L3"


def test_cli_skips_sign_when_reproduce_fails(tmp_path: Path, capsys):
    from farm_notary.cli import main
    from farm_notary.reproduce import ReproductionResult
    from tests.test_reproduce import make_notarized_run

    run_dir, _manifest, _ = make_notarized_run(tmp_path)
    failed = ReproductionResult(command="x", returncode=1, mismatched=["summary.csv"])
    with (
        patch("farm_notary.reproduce.reproduce_run", return_value=failed),
        patch("farm_notary.cli.sign_receipt") as mock_sign,
    ):
        ret = main(["reproduce", "--run-dir", str(run_dir), "--sign"])
    assert ret == 1
    mock_sign.assert_not_called()
    assert "skipping Sigstore sign" in capsys.readouterr().err


def test_cli_rejects_raw_identity_token(tmp_path: Path, capsys):
    from farm_notary.cli import main
    from tests.test_reproduce import make_notarized_run

    run_dir, _manifest, _ = make_notarized_run(tmp_path)
    with patch("farm_notary.cli.sign_receipt") as mock_sign:
        ret = main(
            [
                "reproduce",
                "--run-dir",
                str(run_dir),
                "--sign",
                "--identity-token",
                "eyJhbGciOi.e30.sig",
            ]
        )
    assert ret == 2
    mock_sign.assert_not_called()
    assert "@PATH" in capsys.readouterr().err

