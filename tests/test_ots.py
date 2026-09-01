from pathlib import Path

import pytest
from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import Timestamp

from farm_notary.manifest import build_manifest
from farm_notary.ots import (
    DEFAULT_CALENDARS,
    OpenTimestampsBackend,
    OtsError,
    calendar_urls,
    deserialize_proof,
    proof_status,
    serialize_proof,
    upgrade_proof,
    verify_proof,
)


def make_manifest(tmp_path: Path):
    (tmp_path / "summary.csv").write_text("paradigm,total\nparty,0.2\n", encoding="utf-8")
    return build_manifest(
        tmp_path, publish_patterns=["*.csv"], git_sha="abc", git_dirty=False
    )


def serialize_timestamp(timestamp) -> bytes:
    ctx = BytesSerializationContext()
    timestamp.serialize(ctx)
    return ctx.getbytes()


def pending_timestamp(digest: bytes, uri: str) -> Timestamp:
    timestamp = Timestamp(digest)
    timestamp.attestations.add(PendingAttestation(uri))
    return timestamp


def bitcoin_timestamp(digest: bytes, height: int) -> Timestamp:
    timestamp = Timestamp(digest)
    timestamp.attestations.add(BitcoinBlockHeaderAttestation(height))
    return timestamp


def mixed_pending_timestamp(digest: bytes, *uris: str) -> Timestamp:
    timestamp = Timestamp(digest)
    for uri in uris:
        timestamp.attestations.add(PendingAttestation(uri))
    return timestamp


def test_backend_submits_digest_and_returns_proof(stub_server, tmp_path: Path):
    manifest = make_manifest(tmp_path)
    digest = bytes.fromhex(manifest.content_hash())
    stub_server.response_body = serialize_timestamp(
        pending_timestamp(digest, stub_server.url)
    )

    backend = OpenTimestampsBackend(calendars=[stub_server.url])
    receipt = backend.submit(manifest, cid="bafytest")

    assert receipt.backend == "opentimestamps"
    assert receipt.dry_run is False
    assert receipt.manifest_hash == manifest.content_hash()
    assert receipt.detail["calendars"] == [stub_server.url]
    assert receipt.detail["status"] == "pending"

    request = stub_server.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/digest"
    assert request["body"] == digest

    detached = deserialize_proof(receipt.proof)
    assert detached.file_digest == digest


def test_backend_fails_when_no_calendar_reachable(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    backend = OpenTimestampsBackend(calendars=["http://127.0.0.1:1"], timeout=0.5)
    with pytest.raises(OtsError, match="no calendar accepted"):
        backend.submit(manifest)


def test_calendar_urls_from_env(monkeypatch):
    monkeypatch.setenv("FARM_NOTARY_CALENDARS", "http://one, http://two")
    assert calendar_urls() == ["http://one", "http://two"]
    assert calendar_urls(["http://explicit"]) == ["http://explicit"]


def test_verify_proof_matches_and_detects_mismatch(tmp_path: Path):
    manifest = make_manifest(tmp_path)
    digest = bytes.fromhex(manifest.content_hash())
    proof = serialize_proof(pending_timestamp(digest, "https://example.com"))

    assert verify_proof(proof, manifest.content_hash()) == []
    problems = verify_proof(proof, "ff" * 32)
    assert len(problems) == 1
    assert "proof commits to" in problems[0]


def test_verify_proof_rejects_garbage():
    problems = verify_proof(b"not a proof", "aa" * 32)
    assert problems and "not a valid OpenTimestamps proof" in problems[0]


def test_proof_status_summary(tmp_path: Path):
    digest = b"\xaa" * 32
    pending = serialize_proof(pending_timestamp(digest, "https://example.com"))
    status = proof_status(pending)
    assert not status.confirmed
    assert status.pending_calendars == ["https://example.com"]
    assert any("pending at calendar" in line for line in status.summary())

    confirmed = serialize_proof(bitcoin_timestamp(digest, 800000))
    status = proof_status(confirmed)
    assert status.confirmed
    assert status.bitcoin_heights == [800000]
    assert status.summary() == ["anchored in Bitcoin block 800000"]


def test_proof_status_splits_public_and_user_supplied_pending_calendars():
    digest = b"\xaa" * 32
    proof = serialize_proof(pending_timestamp(digest, DEFAULT_CALENDARS[0]))
    status = proof_status(proof)
    assert status.public_pending_calendars == [DEFAULT_CALENDARS[0]]
    assert status.unknown_pending_calendars == []

    proof = serialize_proof(pending_timestamp(digest, "https://example.com"))
    status = proof_status(proof)
    assert status.public_pending_calendars == []
    assert status.unknown_pending_calendars == ["https://example.com"]


def test_upstream_calendars_of_the_public_pools_count_as_public():
    """A proof from the default submission path names upstream calendars.

    Submitting to the DEFAULT_CALENDARS pools yields PendingAttestations that
    name the servers the pools forward to, never the pool URL. Treating those
    as user-supplied labelled every proof FarmNotary produced by default as
    "untrusted until Bitcoin".
    """
    digest = b"\xaa" * 32
    for uri in (
        "https://alice.btc.calendar.opentimestamps.org",
        "https://bob.btc.calendar.opentimestamps.org",
        "https://finney.calendar.eternitywall.com",
        "https://btc.calendar.catallaxy.com",
    ):
        status = proof_status(serialize_proof(pending_timestamp(digest, uri)))
        assert status.public_pending_calendars == [uri]
        assert status.unknown_pending_calendars == []


def test_upgrade_proof_completes_pending_attestation(stub_server):
    digest = b"\xaa" * 32
    proof = serialize_proof(pending_timestamp(digest, stub_server.url))
    stub_server.get_responses[f"/timestamp/{digest.hex()}"] = serialize_timestamp(
        bitcoin_timestamp(digest, 800000)
    )

    upgraded, status, errors = upgrade_proof(proof)

    assert errors == []
    assert status.confirmed
    assert status.bitcoin_heights == [800000]
    assert status.pending_calendars == []
    # The upgraded proof round-trips.
    assert proof_status(upgraded).confirmed


def test_upgrade_proof_still_pending(stub_server):
    digest = b"\xaa" * 32
    proof = serialize_proof(pending_timestamp(digest, stub_server.url))
    # No canned GET response: the calendar 404s (commitment not aggregated yet).

    upgraded, status, errors = upgrade_proof(proof)

    assert not status.confirmed
    assert status.pending_calendars == [stub_server.url]
    assert errors and "not yet committed" in errors[0]
    assert upgraded == proof


# ---------------------------------------------------------------------------
# CID binding proof
# ---------------------------------------------------------------------------

def test_cid_binding_digest_is_deterministic():
    from farm_notary.ots import cid_binding_digest

    d1 = cid_binding_digest("aa" * 32, "bafytest")
    d2 = cid_binding_digest("aa" * 32, "bafytest")
    assert d1 == d2
    assert len(d1) == 32


def test_cid_binding_digest_changes_with_cid_or_hash():
    from farm_notary.ots import cid_binding_digest

    base = cid_binding_digest("aa" * 32, "bafytest")
    assert cid_binding_digest("bb" * 32, "bafytest") != base
    assert cid_binding_digest("aa" * 32, "bafyOTHER") != base


def test_verify_cid_binding_proof_accepts_correct_proof():
    from farm_notary.ots import cid_binding_digest, serialize_proof, verify_cid_binding_proof

    manifest_hash = "aa" * 32
    cid = "bafytest"
    digest = cid_binding_digest(manifest_hash, cid)
    proof = serialize_proof(pending_timestamp(digest, "https://example.com"))

    assert verify_cid_binding_proof(proof, manifest_hash, cid) == []


def test_verify_cid_binding_proof_rejects_wrong_cid():
    from farm_notary.ots import cid_binding_digest, serialize_proof, verify_cid_binding_proof

    manifest_hash = "aa" * 32
    correct_cid = "bafyCORRECT"
    swapped_cid = "bafySWAPPED"
    digest = cid_binding_digest(manifest_hash, correct_cid)
    proof = serialize_proof(pending_timestamp(digest, "https://example.com"))

    problems = verify_cid_binding_proof(proof, manifest_hash, swapped_cid)
    assert len(problems) == 1
    assert "CID binding proof commits to" in problems[0]


def test_verify_cid_binding_proof_rejects_garbage():
    from farm_notary.ots import verify_cid_binding_proof

    problems = verify_cid_binding_proof(b"not-a-proof", "aa" * 32, "bafytest")
    assert problems and "not a valid OpenTimestamps proof" in problems[0]
