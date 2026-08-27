"""OpenTimestamps anchoring.

The anchoring layer is outsourced: public calendar servers aggregate digests
and commit them into Bitcoin. Free, keyless, no contract to run. Requires the
opentimestamps library: install ``farm-notary[ots]``.

The proof (``manifest.ots``) commits to the manifest *content hash* — the
SHA-256 of the canonical manifest body excluding ``cid`` and ``anchor`` — so
stamping the manifest with upload/anchor results never invalidates it.
Proofs start out pending; once a calendar batches the digest into a Bitcoin
transaction (typically within hours), ``farm-notary upgrade`` completes them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

PROOF_NAME = "manifest.ots"

DEFAULT_CALENDARS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
)


class OtsError(RuntimeError):
    pass


def _require_ots():
    try:
        import opentimestamps  # noqa: F401
    except ImportError as exc:
        raise OtsError(
            "OpenTimestamps anchoring needs the opentimestamps library; "
            "install farm-notary[ots]"
        ) from exc


def calendar_urls(calendars=None) -> List[str]:
    if calendars:
        return list(calendars)
    env = os.environ.get("FARM_NOTARY_CALENDARS")
    if env:
        return [url.strip() for url in env.split(",") if url.strip()]
    return list(DEFAULT_CALENDARS)


def serialize_proof(timestamp) -> bytes:
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import BytesSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    ctx = BytesSerializationContext()
    DetachedTimestampFile(OpSHA256(), timestamp).serialize(ctx)
    return ctx.getbytes()


def deserialize_proof(proof: bytes):
    _require_ots()
    from opentimestamps.core.serialize import (
        BadMagicError,
        BytesDeserializationContext,
        DeserializationError,
    )
    from opentimestamps.core.timestamp import DetachedTimestampFile

    try:
        return DetachedTimestampFile.deserialize(BytesDeserializationContext(proof))
    except (BadMagicError, DeserializationError) as exc:
        raise OtsError(f"not a valid OpenTimestamps proof: {exc}") from exc


def _walk(timestamp):
    yield timestamp
    for child in timestamp.ops.values():
        yield from _walk(child)


@dataclass
class ProofStatus:
    digest: str
    bitcoin_heights: List[int] = field(default_factory=list)
    pending_calendars: List[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return bool(self.bitcoin_heights)

    def summary(self) -> List[str]:
        lines = []
        for height in sorted(set(self.bitcoin_heights)):
            lines.append(f"anchored in Bitcoin block {height}")
        for uri in sorted(set(self.pending_calendars)):
            lines.append(f"pending at calendar {uri} (run `farm-notary upgrade` later)")
        if not lines:
            lines.append("proof has no attestations")
        return lines


def proof_status(proof: bytes) -> ProofStatus:
    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation,
        PendingAttestation,
    )

    detached = deserialize_proof(proof)
    status = ProofStatus(digest=detached.file_digest.hex())
    for _, attestation in detached.timestamp.all_attestations():
        if isinstance(attestation, BitcoinBlockHeaderAttestation):
            status.bitcoin_heights.append(attestation.height)
        elif isinstance(attestation, PendingAttestation):
            status.pending_calendars.append(attestation.uri)
    return status


def verify_proof(proof: bytes, manifest_hash: str) -> List[str]:
    """Check that the proof commits to the given manifest content hash."""
    try:
        status = proof_status(proof)
    except OtsError as exc:
        return [str(exc)]
    problems = []
    if status.digest != manifest_hash:
        problems.append(
            f"proof commits to {status.digest}, manifest content hash is {manifest_hash}"
        )
    if not status.confirmed and not status.pending_calendars:
        problems.append("proof has no attestations")
    return problems


def upgrade_proof(proof: bytes, timeout: float = 10.0) -> Tuple[bytes, ProofStatus, List[str]]:
    """Ask calendars for Bitcoin attestations of pending commitments.

    Returns the (possibly upgraded) proof bytes, its status, and any errors
    from calendars that could not be reached or don't have the commitment yet.
    """
    from opentimestamps.calendar import CommitmentNotFoundError, RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation

    detached = deserialize_proof(proof)
    errors: List[str] = []
    for node in _walk(detached.timestamp):
        for attestation in list(node.attestations):
            if not isinstance(attestation, PendingAttestation):
                continue
            try:
                upgraded = RemoteCalendar(attestation.uri).get_timestamp(
                    node.msg, timeout=timeout
                )
            except CommitmentNotFoundError:
                errors.append(f"{attestation.uri}: not yet committed to Bitcoin")
                continue
            except Exception as exc:
                errors.append(f"{attestation.uri}: {exc}")
                continue
            node.merge(upgraded)
            node.attestations.remove(attestation)
    upgraded_proof = serialize_proof(detached.timestamp)
    return upgraded_proof, proof_status(upgraded_proof), errors


def stamp_digest(
    digest: bytes, calendars=None, timeout: float = 10.0
) -> Tuple[bytes, List[str]]:
    """Submit a 32-byte digest to calendars; return (proof bytes, accepted urls)."""
    _require_ots()
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.timestamp import Timestamp

    timestamp = Timestamp(digest)
    accepted: List[str] = []
    errors: List[str] = []
    for url in calendar_urls(calendars):
        try:
            timestamp.merge(RemoteCalendar(url).submit(digest, timeout=timeout))
            accepted.append(url)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if not accepted:
        raise OtsError("no calendar accepted the digest: " + "; ".join(errors))
    return serialize_proof(timestamp), accepted


class OpenTimestampsBackend:
    """Anchor backend using public OpenTimestamps calendar servers."""

    def __init__(self, calendars=None, timeout: float = 10.0):
        _require_ots()
        self.calendars = calendar_urls(calendars)
        self.timeout = timeout

    def submit(self, manifest, *, cid: Optional[str] = None):
        from farm_notary.anchor import AnchorReceipt

        manifest_hash = manifest.content_hash()
        proof, accepted = stamp_digest(
            bytes.fromhex(manifest_hash), self.calendars, timeout=self.timeout
        )
        return AnchorReceipt(
            backend="opentimestamps",
            manifest_hash=manifest_hash,
            cid=cid,
            dry_run=False,
            detail={"calendars": accepted, "proof": PROOF_NAME, "status": "pending"},
            proof=proof,
        )
