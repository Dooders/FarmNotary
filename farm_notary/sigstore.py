"""Sigstore keyless signing for reproduction receipts (optional feature).

Uses ``cosign sign-blob`` (OIDC keyless). The Rekor inclusion proof and cert
chain are bundled inside the receipt JSON under the ``"sigstore"`` key so
offline verification works without a live Rekor round-trip.

``cosign`` is an optional runtime dependency: if it is not on PATH the signing
step fails with a clear message; verification falls back to a note rather than
a hard failure (the receipt is still "self-attested").

Identity tokens are passed to cosign via ``SIGSTORE_ID_TOKEN`` (never argv).
A receipt signature proves that Fulcio issued a cert for *some* OIDC identity.
It does not prove independence from the publisher.

Docs note
---------
A receipt count is **not** credibility.  Ten throwaway Gmail reproductions are
not equivalent to one lab CI reproduction.  Inspect ``issuer`` when it can be
parsed.  Missing identity notes mean the cert could not be read, not that the
signer is trusted.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from cryptography import x509 as _x509
    from cryptography.x509 import ObjectIdentifier as _ObjectIdentifier
except ImportError:  # pragma: no cover — [sigstore] extra not installed
    _x509 = None
    _ObjectIdentifier = None

from farm_notary.manifest import hash_json

SIGSTORE_FIELD = "sigstore"
SIGSTORE_ID_TOKEN_ENV = "SIGSTORE_ID_TOKEN"
COSIGN_IDENTITY_TOKEN_ENV = "COSIGN_IDENTITY_TOKEN"

# Pinned for docs and the GitHub Action. Bump here and in action.yml together.
COSIGN_RELEASE = "v2.5.3"

SIGN_TIMEOUT_SEC = 120
VERIFY_TIMEOUT_SEC = 60

# Fulcio OIDC issuer extensions (v1 deprecated; v2 is current).
OIDC_ISSUER_OID = "1.3.6.1.4.1.57264.1.1"
OIDC_ISSUER_V2_OID = "1.3.6.1.4.1.57264.1.8"


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
            "https://github.com/sigstore/cosign/releases "
            f"(documented pin: {COSIGN_RELEASE})"
        )
    return path


def receipt_payload(receipt: dict) -> dict:
    """Receipt fields that OTS and Sigstore both commit to."""
    return {k: v for k, v in receipt.items() if k != SIGSTORE_FIELD}


def receipt_signable_bytes(receipt: dict) -> bytes:
    """Return the canonical JSON bytes of *receipt* that are signed and hashed.

    Same encoding as :func:`farm_notary.manifest.hash_json` (sorted keys,
    compact separators) so the OTS digest and the cosign blob match.
    The ``"sigstore"`` field is excluded.
    """
    return json.dumps(
        receipt_payload(receipt), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def receipt_content_hash(receipt: dict) -> str:
    """SHA-256 of :func:`receipt_signable_bytes` (hex)."""
    return hash_json(receipt_payload(receipt))


def resolve_identity_token(
    identity_token: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Resolve a token from an explicit value or the process environment.

    *identity_token* is a JWT or empty. Callers that accept CLI input must
    reject a raw JWT and pass file contents or ``None`` (env only).
    """
    if identity_token:
        return identity_token
    environ = env if env is not None else os.environ
    return environ.get(COSIGN_IDENTITY_TOKEN_ENV) or environ.get(SIGSTORE_ID_TOKEN_ENV)


def read_identity_token_cli(value: Optional[str]) -> Optional[str]:
    """CLI helper: ``@PATH`` only. A raw JWT is rejected so it never hits argv.

    When *value* is omitted, cosign still sees ``SIGSTORE_ID_TOKEN`` /
    ``COSIGN_IDENTITY_TOKEN`` in the environment if the operator set them.
    """
    if not value:
        return None
    if not value.startswith("@"):
        raise SigstoreError(
            "--identity-token must be @PATH; set COSIGN_IDENTITY_TOKEN or "
            "SIGSTORE_ID_TOKEN instead of passing a raw JWT on the command line"
        )
    path = Path(value[1:])
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SigstoreError(f"could not read identity token file {path}: {exc}") from exc


def bundle_has_inclusion_proof(bundle: Any) -> bool:
    """True when *bundle* looks like it carries a Rekor inclusion proof."""
    if not isinstance(bundle, dict):
        return False
    if bundle.get("rekorBundle"):
        return True
    vm = bundle.get("verificationMaterial")
    if not isinstance(vm, dict):
        return False
    if vm.get("tlogEntries") or vm.get("tlogEntry") or vm.get("tlog"):
        return True
    return False


def _cosign_env(identity_token: Optional[str]) -> dict:
    env = os.environ.copy()
    token = resolve_identity_token(identity_token, env=env)
    if token:
        env[SIGSTORE_ID_TOKEN_ENV] = token
        env[COSIGN_IDENTITY_TOKEN_ENV] = token
    return env


def sign_receipt(
    receipt: dict,
    *,
    identity_token: Optional[str] = None,
) -> dict:
    """Sign *receipt* with ``cosign sign-blob`` (keyless OIDC) and return the bundle.

    The returned bundle dict contains the Rekor tlog entry and the certificate
    chain.  Embed it in the receipt under ``receipt["sigstore"]`` and rewrite
    the receipt file.

    *identity_token* is placed in ``SIGSTORE_ID_TOKEN`` for the child process.
    It is never passed as ``--identity-token`` on argv.

    Raises :exc:`SigstoreError` when cosign is not on PATH or signing fails.
    """
    cosign = _require_cosign()
    blob_bytes = receipt_signable_bytes(receipt)

    with tempfile.TemporaryDirectory(prefix="farm-notary-sigstore-") as tmp:
        blob_path = Path(tmp) / "receipt.json"
        blob_path.write_bytes(blob_bytes)
        bundle_path = Path(tmp) / "bundle.json"

        cmd = [
            cosign,
            "sign-blob",
            "--bundle",
            str(bundle_path),
            "--yes",
            str(blob_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=SIGN_TIMEOUT_SEC,
                env=_cosign_env(identity_token),
            )
        except subprocess.TimeoutExpired as exc:
            raise SigstoreError(
                f"cosign sign-blob timed out after {SIGN_TIMEOUT_SEC}s"
            ) from exc
        if result.returncode != 0:
            raise SigstoreError(result.stderr.strip() or "cosign sign-blob failed")
        if not bundle_path.is_file():
            raise SigstoreError("cosign sign-blob did not produce a bundle file")

        raw = bundle_path.read_text(encoding="utf-8")
        try:
            bundle = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SigstoreError(f"cosign produced malformed bundle JSON: {exc}") from exc
        if not isinstance(bundle, dict):
            raise SigstoreError(
                f"cosign bundle is not a JSON object (got {type(bundle).__name__})"
            )
        return bundle


def verify_sigstore_bundle(
    bundle: dict,
    receipt: dict,
) -> Tuple[List[str], Dict[str, str]]:
    """Verify the Sigstore bundle against *receipt*.

    Uses ``--offline`` when the bundle has a Rekor inclusion proof. Otherwise
    a live Rekor check is attempted. There is no silent fallback the other way.

    Returns ``(problems, identity_info)``.  *problems* is empty on success.
    *identity_info* contains ``"subject"`` and ``"issuer"`` when the cert
    chain can be parsed.

    **Identity constraints**: this function uses ``--certificate-identity-regexp .*``
    and ``--certificate-oidc-issuer-regexp .*`` so it proves that the bytes were
    signed by *someone* via Sigstore, not by a specific trusted identity.

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
    has_proof = bundle_has_inclusion_proof(bundle)

    with tempfile.TemporaryDirectory(prefix="farm-notary-sigstore-verify-") as tmp:
        blob_path = Path(tmp) / "receipt.json"
        blob_path.write_bytes(blob_bytes)
        bundle_path = Path(tmp) / "bundle.json"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

        cmd = [
            cosign,
            "verify-blob",
            "--bundle",
            str(bundle_path),
            "--certificate-identity-regexp",
            ".*",
            "--certificate-oidc-issuer-regexp",
            ".*",
        ]
        if has_proof:
            cmd.append("--offline")
        cmd.append(str(blob_path))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=VERIFY_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return (
                [f"cosign verify-blob timed out after {VERIFY_TIMEOUT_SEC}s"],
                {},
            )
        if result.returncode != 0:
            detail = result.stderr.strip() or "signature rejected"
            if not has_proof:
                return (
                    [
                        "bundle has no Rekor inclusion proof; offline verify not "
                        f"possible; live verify failed: {detail}"
                    ],
                    {},
                )
            return ([f"cosign verify-blob: {detail}"], {})

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


def _decode_fulcio_issuer(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    # DER UTF8String: 0x0c <len> <bytes>
    if len(raw) >= 2 and raw[0] == 0x0C:
        length = raw[1]
        if length <= len(raw) - 2:
            return raw[2 : 2 + length].decode("utf-8", errors="replace")
    return text


def _extract_identity(bundle: dict) -> Dict[str, str]:
    try:
        certs = bundle["verificationMaterial"]["x509CertificateChain"]["certificates"]
        raw_b64 = certs[0]["rawBytes"]
    except (KeyError, IndexError, TypeError):
        return {}

    try:
        der_bytes = base64.b64decode(raw_b64)
    except (ValueError, TypeError):
        return {}

    if _x509 is None or _ObjectIdentifier is None:
        return {}

    try:
        cert = _x509.load_der_x509_certificate(der_bytes)
    except Exception:
        return {}

    subject = ""
    try:
        san = cert.extensions.get_extension_for_class(_x509.SubjectAlternativeName)
        uris = san.value.get_values_for_type(_x509.UniformResourceIdentifier)
        emails = san.value.get_values_for_type(_x509.RFC822Name)
        subject = uris[0] if uris else (emails[0] if emails else "")
    except Exception:
        pass

    issuer = ""
    for oid in (OIDC_ISSUER_V2_OID, OIDC_ISSUER_OID):
        try:
            ext = cert.extensions.get_extension_for_oid(_ObjectIdentifier(oid))
            raw = ext.value.value if hasattr(ext.value, "value") else bytes(ext.value)
            issuer = _decode_fulcio_issuer(raw)
            if issuer:
                break
        except Exception:
            continue

    if not subject and not issuer:
        return {}
    return {"subject": subject, "issuer": issuer}
