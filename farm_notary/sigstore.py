"""Sigstore keyless signing for reproduction receipts (optional feature).

Uses ``cosign sign-blob`` (OIDC keyless). The Rekor inclusion proof and cert
chain are bundled inside the receipt JSON under the ``"sigstore"`` key so
offline verification works without a live Rekor round-trip.

``cosign`` is an optional runtime dependency: if it is not on PATH the signing
step fails with a clear message; verification falls back to a note rather than
a hard failure (the receipt is still "self-attested").

Docs note
---------
A receipt count is **not** credibility.  Ten throwaway Gmail reproductions are
not equivalent to one lab CI reproduction.  Inspect ``issuer`` to distinguish
workload-identity CI tokens from personal OIDC logins.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SIGSTORE_FIELD = "sigstore"


class SigstoreError(RuntimeError):
    pass


def cosign_available() -> bool:
    """Return *True* when ``cosign`` is on PATH."""
    return shutil.which("cosign") is not None


def _require_cosign() -> str:
    path = shutil.which("cosign")
    if not path:
        raise SigstoreError(
            "cosign is not on PATH; install it from "
            "https://github.com/sigstore/cosign/releases"
        )
    return path


def receipt_signable_bytes(receipt: dict) -> bytes:
    """Return the canonical JSON bytes of *receipt* that are signed and verified.

    The ``"sigstore"`` field is excluded so that adding the bundle after signing
    does not invalidate the signature and so that the bytes are stable across
    receipt round-trips (parse → strip → serialize).
    """
    d = {k: v for k, v in receipt.items() if k != SIGSTORE_FIELD}
    return (json.dumps(d, indent=2) + "\n").encode("utf-8")


def sign_receipt(
    receipt: dict,
    *,
    identity_token: Optional[str] = None,
) -> dict:
    """Sign *receipt* with ``cosign sign-blob`` (keyless OIDC) and return the bundle.

    The returned bundle dict contains the Rekor tlog entry and the certificate
    chain.  Embed it in the receipt under ``receipt["sigstore"]`` and rewrite
    the receipt file.

    ``identity_token`` can be set to a GitHub Actions OIDC token
    (``$ACTIONS_ID_TOKEN_REQUEST_TOKEN`` resolved via the Actions API) for
    non-interactive CI signing.

    Raises :exc:`SigstoreError` when cosign is not on PATH or signing fails.
    """
    cosign = _require_cosign()
    blob_bytes = receipt_signable_bytes(receipt)

    with tempfile.TemporaryDirectory(prefix="farm-notary-sigstore-") as tmp:
        blob_path = Path(tmp) / "receipt.json"
        blob_path.write_bytes(blob_bytes)
        bundle_path = Path(tmp) / "bundle.json"

        cmd = [
            cosign, "sign-blob",
            "--bundle", str(bundle_path),
            "--yes",
            str(blob_path),
        ]
        if identity_token:
            cmd += ["--identity-token", identity_token]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SigstoreError(result.stderr.strip() or "cosign sign-blob failed")
        if not bundle_path.is_file():
            raise SigstoreError("cosign sign-blob did not produce a bundle file")

        return json.loads(bundle_path.read_text(encoding="utf-8"))


def verify_sigstore_bundle(
    bundle: dict,
    receipt: dict,
) -> Tuple[List[str], Dict[str, str]]:
    """Verify the Sigstore bundle against *receipt* (offline-first).

    Uses the bundled Rekor tlog entry and cert chain; a live round-trip is only
    attempted when the bundle contains no inclusion proof.

    Returns ``(problems, identity_info)``.  *problems* is empty on success.
    *identity_info* contains ``"subject"`` (SAN URI or email) and ``"issuer"``
    (OIDC issuer URL) when the cert chain can be parsed.

    **Identity constraints**: this function uses ``--certificate-identity-regexp .*``
    and ``--certificate-oidc-issuer-regexp .*`` so it proves that the bytes were
    signed by *someone* via Sigstore, not by a specific trusted identity.  The
    caller is responsible for inspecting *identity_info* and presenting the signer
    to the user; count is **not** credibility.

    If ``cosign`` is not on PATH, returns a single informational problem so the
    caller can decide whether to treat it as a hard failure.
    """
    cosign = shutil.which("cosign")
    if not cosign:
        return (
            ["cosign not on PATH; install it to verify the Sigstore bundle"],
            {},
        )

    blob_bytes = receipt_signable_bytes(receipt)

    with tempfile.TemporaryDirectory(prefix="farm-notary-sigstore-verify-") as tmp:
        blob_path = Path(tmp) / "receipt.json"
        blob_path.write_bytes(blob_bytes)
        bundle_path = Path(tmp) / "bundle.json"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

        cmd = [
            cosign, "verify-blob",
            "--bundle", str(bundle_path),
            "--certificate-identity-regexp", ".*",
            "--certificate-oidc-issuer-regexp", ".*",
            str(blob_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return (
                [f"cosign verify-blob: {result.stderr.strip() or 'signature rejected'}"],
                {},
            )

    identity_info = _extract_identity(bundle)
    return [], identity_info


def extract_bundle_identity(bundle: dict) -> Dict[str, str]:
    """Extract subject and issuer from the bundle cert chain (no subprocess).

    Returns a dict with ``"subject"`` (SAN URI/email) and ``"issuer"``
    (OIDC issuer URL) when the ``cryptography`` package is available.
    Falls back to empty strings when it is not installed or parsing fails.
    This is a best-effort extraction; it does not verify the bundle.
    """
    return _extract_identity(bundle)


def _extract_identity(bundle: dict) -> Dict[str, str]:
    try:
        certs = bundle["verificationMaterial"]["x509CertificateChain"]["certificates"]
        raw_b64 = certs[0]["rawBytes"]
    except (KeyError, IndexError, TypeError):
        return {}

    import base64

    try:
        der_bytes = base64.b64decode(raw_b64)
    except Exception:
        return {}

    try:
        from cryptography import x509 as _x509

        cert = _x509.load_der_x509_certificate(der_bytes)

        subject = ""
        try:
            san = cert.extensions.get_extension_for_class(_x509.SubjectAlternativeName)
            uris = san.value.get_values_for_type(_x509.UniformResourceIdentifier)
            emails = san.value.get_values_for_type(_x509.RFC822Name)
            subject = uris[0] if uris else (emails[0] if emails else "")
        except Exception:
            pass

        issuer = ""
        try:
            from cryptography.x509 import ObjectIdentifier

            OIDC_ISSUER_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.1")
            ext = cert.extensions.get_extension_for_oid(OIDC_ISSUER_OID)
            issuer = ext.value.value.decode("utf-8")
        except Exception:
            pass

        return {"subject": subject, "issuer": issuer}
    except ImportError:
        pass

    return {}
