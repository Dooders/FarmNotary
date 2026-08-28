"""Optional lab identity: sign the content hash, still no protocol token.

Reviewers who already know the lab's minisign or SSH public key get
“this lab published these bytes.”  Everyone else still has OpenTimestamps.
The signature is recorded on the manifest and excluded from ``content_hash``,
so stamping identity after the hash is computed does not circular-hash.

EAS remains experimental and is not used here.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

SCHEME_SSH = "ssh"
SCHEME_MINISIGN = "minisign"
VALID_SCHEMES = (SCHEME_SSH, SCHEME_MINISIGN)
SSH_NAMESPACE = "farm-notary"


class IdentityError(RuntimeError):
    pass


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise IdentityError(
            f"{name} is not on PATH; install it to use --scheme {name}"
            if name == "minisign"
            else f"{name} is not on PATH"
        )
    return path


def _public_key_text(key_path: Path, scheme: str) -> str:
    key_path = Path(key_path)
    if scheme == SCHEME_SSH:
        pub = key_path if key_path.name.endswith(".pub") else Path(str(key_path) + ".pub")
        if pub.is_file():
            return pub.read_text(encoding="utf-8").strip()
        # ssh-keygen can print the public half of a private key
        result = subprocess.run(
            [_require_tool("ssh-keygen"), "-y", "-f", str(key_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IdentityError(result.stderr.strip() or "could not read SSH public key")
        return result.stdout.strip()
    # minisign: companion .pub next to the secret key, or the path is already the pub
    if key_path.suffix == ".pub" or key_path.name.endswith(".pub"):
        return key_path.read_text(encoding="utf-8").strip()
    pub = key_path.with_suffix(key_path.suffix + ".pub") if key_path.suffix else Path(str(key_path) + ".pub")
    if not pub.is_file():
        pub = Path(str(key_path) + ".pub")
    if pub.is_file():
        return pub.read_text(encoding="utf-8").strip()
    raise IdentityError(f"minisign public key not found next to {key_path}")


def _ssh_principal(public_key: str, principal: Optional[str]) -> str:
    if principal:
        return principal
    parts = public_key.split()
    if len(parts) >= 3:
        return parts[-1]
    return "farm-notary"


def sign_content_hash(
    content_hash: str,
    *,
    scheme: str,
    key_path: Path,
    principal: Optional[str] = None,
) -> dict:
    """Sign ``content_hash`` (hex) and return an identity record."""
    if scheme not in VALID_SCHEMES:
        raise IdentityError(f"unknown identity scheme {scheme!r} (expected ssh or minisign)")
    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash.lower()):
        raise IdentityError("content_hash must be a 64-character hex SHA-256")

    key_path = Path(key_path)
    if not key_path.is_file():
        raise IdentityError(f"key not found: {key_path}")

    public_key = _public_key_text(key_path, scheme)
    payload = content_hash.lower().encode("ascii")

    with tempfile.TemporaryDirectory(prefix="farm-notary-sign-") as tmp:
        message = Path(tmp) / "content_hash"
        message.write_bytes(payload)
        if scheme == SCHEME_SSH:
            signature = _sign_ssh(message, key_path)
            ident = _ssh_principal(public_key, principal)
        else:
            signature = _sign_minisign(message, key_path)
            ident = principal
        record = {
            "scheme": scheme,
            "public_key": public_key,
            "signature": signature,
            "signed": content_hash.lower(),
        }
        if ident:
            record["principal"] = ident
        if scheme == SCHEME_SSH:
            record["namespace"] = SSH_NAMESPACE
        return record


def _sign_ssh(message: Path, key_path: Path) -> str:
    ssh_keygen = _require_tool("ssh-keygen")
    # Signing key must be the private key.  If the caller passed the .pub, strip it.
    private = key_path
    if key_path.name.endswith(".pub"):
        private = Path(str(key_path)[:-4])
        if not private.is_file():
            raise IdentityError(f"SSH private key not found for {key_path}")
    result = subprocess.run(
        [ssh_keygen, "-Y", "sign", "-f", str(private), "-n", SSH_NAMESPACE, str(message)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise IdentityError(result.stderr.strip() or "ssh-keygen -Y sign failed")
    sig_path = Path(str(message) + ".sig")
    if not sig_path.is_file():
        raise IdentityError("ssh-keygen did not write a signature file")
    return sig_path.read_text(encoding="utf-8")


def _sign_minisign(message: Path, key_path: Path) -> str:
    minisign = _require_tool("minisign")
    result = subprocess.run(
        [minisign, "-S", "-s", str(key_path), "-m", str(message), "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise IdentityError(result.stderr.strip() or "minisign -S failed")
    sig_path = Path(str(message) + ".minisig")
    if not sig_path.is_file():
        raise IdentityError("minisign did not write a signature file")
    return sig_path.read_text(encoding="utf-8")


def verify_identity(identity: Optional[dict], content_hash: str) -> List[str]:
    """Check that ``identity`` is a valid signature of ``content_hash``.

    Returns an empty list when *identity* is absent (optional) or verifies.
    """
    if not identity:
        return []
    if not isinstance(identity, dict):
        return ["identity record is not an object"]
    scheme = identity.get("scheme")
    if scheme not in VALID_SCHEMES:
        return [f"unknown identity scheme {scheme!r}"]
    signed = (identity.get("signed") or "").lower()
    if signed and signed != content_hash.lower():
        return [
            f"identity signed {signed}, manifest content hash is {content_hash.lower()}"
        ]
    signature = identity.get("signature")
    public_key = identity.get("public_key")
    if not signature or not public_key:
        return ["identity record is missing signature or public_key"]

    try:
        if scheme == SCHEME_SSH:
            _verify_ssh(content_hash, signature, public_key, identity.get("principal"))
        else:
            _verify_minisign(content_hash, signature, public_key)
    except IdentityError as exc:
        return [f"identity: {exc}"]
    return []


def _verify_ssh(content_hash: str, signature: str, public_key: str, principal: Optional[str]) -> None:
    ssh_keygen = _require_tool("ssh-keygen")
    ident = principal or _ssh_principal(public_key, None)
    # allowed_signers: principal <key-type> <base64> [comment]
    pub_line = public_key.strip()
    if pub_line.startswith("ssh-") or pub_line.startswith("ecdsa-") or pub_line.startswith("sk-"):
        allowed_line = f"{ident} {pub_line}"
    else:
        allowed_line = f"{ident} {pub_line}"
    with tempfile.TemporaryDirectory(prefix="farm-notary-id-") as tmp:
        message = Path(tmp) / "content_hash"
        message.write_bytes(content_hash.lower().encode("ascii"))
        sig_path = Path(tmp) / "content_hash.sig"
        sig_path.write_text(signature, encoding="utf-8")
        allowed = Path(tmp) / "allowed_signers"
        allowed.write_text(allowed_line + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "verify",
                "-f",
                str(allowed),
                "-I",
                ident,
                "-n",
                SSH_NAMESPACE,
                "-s",
                str(sig_path),
            ],
            input=content_hash.lower(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IdentityError(result.stderr.strip() or result.stdout.strip() or "SSH signature rejected")


def _verify_minisign(content_hash: str, signature: str, public_key: str) -> None:
    minisign = _require_tool("minisign")
    with tempfile.TemporaryDirectory(prefix="farm-notary-id-") as tmp:
        message = Path(tmp) / "content_hash"
        message.write_bytes(content_hash.lower().encode("ascii"))
        sig_path = Path(tmp) / "content_hash.minisig"
        sig_path.write_text(signature, encoding="utf-8")
        pub_path = Path(tmp) / "minisign.pub"
        pub_path.write_text(public_key if public_key.endswith("\n") else public_key + "\n", encoding="utf-8")
        result = subprocess.run(
            [minisign, "-V", "-p", str(pub_path), "-m", str(message), "-q"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IdentityError(result.stderr.strip() or "minisign signature rejected")


def sign_record(record: Any, *, scheme: str, key_path: Path, principal: Optional[str] = None) -> dict:
    """Stamp ``record.identity`` with a signature of ``record.content_hash()``."""
    identity = sign_content_hash(
        record.content_hash(), scheme=scheme, key_path=key_path, principal=principal
    )
    record.identity = identity
    return identity
