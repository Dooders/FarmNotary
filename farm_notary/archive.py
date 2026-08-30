"""Durable archive helpers.

Optional integrations for long-lived, citable storage.  None of these are
required for the core claim ladder; they are additive.

Zenodo
------
Deposit a run directory (or individual files) to Zenodo and stamp the
returned DOI back onto a manifest.  Uses the Zenodo REST API over plain
``urllib`` to avoid adding dependencies.

Software Heritage
-----------------
Resolve a git commit SHA to a persistent SWH identifier
(``swh:1:rev:<sha1>``).  SWH IDs survive repository deletion and are more
stable than GitHub URLs.

Neither integration requires credentials to *look things up*, but deposits
require an API token (passed as an argument or via environment variable).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from farm_notary.manifest import Manifest

# ---------------------------------------------------------------------------
# Zenodo
# ---------------------------------------------------------------------------

_ZENODO_API_BASE = "https://zenodo.org/api"
_ZENODO_SANDBOX_API_BASE = "https://sandbox.zenodo.org/api"


class ZenodoError(Exception):
    """Raised when a Zenodo API call fails."""


def _zenodo_request(
    method: str,
    url: str,
    *,
    token: str,
    data: Optional[bytes] = None,
    content_type: str = "application/json",
    timeout: int = 30,
) -> dict:
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": content_type,
    }
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ZenodoError(f"Zenodo HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise ZenodoError(f"Zenodo request failed: {exc.reason}") from exc


def zenodo_create_deposit(
    *,
    token: str,
    sandbox: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Create a new (empty) Zenodo deposit and return the API response.

    Parameters
    ----------
    token:
        Zenodo personal access token.
    sandbox:
        Use the Zenodo sandbox instead of production.
    metadata:
        Optional deposit metadata (title, description, creators, …).
        If not supplied a minimal placeholder is used.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    dict
        Full Zenodo API response for the created deposition.
    """
    base = _ZENODO_SANDBOX_API_BASE if sandbox else _ZENODO_API_BASE
    url = f"{base}/deposit/depositions"
    payload: Dict[str, Any] = {"metadata": metadata or {}}
    data = json.dumps(payload).encode()
    return _zenodo_request("POST", url, token=token, data=data, timeout=timeout)


def zenodo_upload_file(
    deposit_id: str,
    file_path: str,
    *,
    token: str,
    sandbox: bool = False,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Upload a single file to an existing Zenodo deposit.

    Parameters
    ----------
    deposit_id:
        Numeric deposition ID returned by :func:`zenodo_create_deposit`.
    file_path:
        Local path to the file to upload.
    token:
        Zenodo personal access token.
    sandbox:
        Use the Zenodo sandbox.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    dict
        Zenodo API response for the uploaded file.
    """
    import mimetypes
    from pathlib import Path

    base = _ZENODO_SANDBOX_API_BASE if sandbox else _ZENODO_API_BASE
    p = Path(file_path)
    url = f"{base}/deposit/depositions/{deposit_id}/files"
    file_bytes = p.read_bytes()
    # Zenodo expects multipart/form-data; we build a minimal boundary.
    boundary = "FarmNotaryBoundary"
    ctype, _ = mimetypes.guess_type(p.name)
    ctype = ctype or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="name"\r\n\r\n{p.name}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{p.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    return _zenodo_request(
        "POST",
        url,
        token=token,
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=timeout,
    )


def zenodo_publish(
    deposit_id: str,
    *,
    token: str,
    sandbox: bool = False,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Publish a Zenodo deposit and return the response (including the DOI).

    Parameters
    ----------
    deposit_id:
        Numeric deposition ID.
    token:
        Zenodo personal access token.
    sandbox:
        Use the Zenodo sandbox.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    dict
        Zenodo API response; ``response["doi"]`` contains the assigned DOI.
    """
    base = _ZENODO_SANDBOX_API_BASE if sandbox else _ZENODO_API_BASE
    url = f"{base}/deposit/depositions/{deposit_id}/actions/publish"
    return _zenodo_request("POST", url, token=token, timeout=timeout)


def deposit_manifest(
    manifest: "Manifest",
    run_dir: str,
    *,
    token: Optional[str] = None,
    sandbox: bool = False,
    files: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    publish: bool = False,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Deposit a run's manifest (and optionally artifacts) to Zenodo.

    Parameters
    ----------
    manifest:
        Built manifest for *run_dir*.
    run_dir:
        Local run directory containing ``manifest.json``.
    token:
        Zenodo token.  Falls back to the ``ZENODO_TOKEN`` environment variable.
    sandbox:
        Use the Zenodo sandbox (for testing).
    files:
        List of relative paths (within *run_dir*) to upload.  If not given,
        only ``manifest.json`` is uploaded.
    metadata:
        Deposit metadata dict.  Defaults to a minimal auto-generated one.
    publish:
        If True, publish the deposit immediately and return the DOI.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    dict
        Zenodo API response.  If *publish* is True, includes ``"doi"``.

    Raises
    ------
    ZenodoError
        On any Zenodo API failure.
    ValueError
        If no token is available.
    """
    from pathlib import Path

    tok = token or os.environ.get("ZENODO_TOKEN", "")
    if not tok:
        raise ValueError(
            "Zenodo token required: pass token= or set ZENODO_TOKEN env var"
        )

    rd = Path(run_dir)
    default_meta: Dict[str, Any] = {
        "upload_type": "dataset",
        "title": f"FarmNotary run {manifest.content_hash()[:12]}",
        "description": (
            f"Automated deposit by FarmNotary. "
            f"Content hash: {manifest.content_hash()}. "
            f"Git SHA: {manifest.git_sha or 'unknown'}."
        ),
        "creators": [{"name": "FarmNotary automated deposit"}],
    }
    if metadata:
        default_meta.update(metadata)

    deposition = zenodo_create_deposit(
        token=tok, sandbox=sandbox, metadata=default_meta, timeout=timeout
    )
    dep_id = str(deposition["id"])

    upload_paths: List[str] = list(files) if files is not None else ["manifest.json"]
    if files is not None and "manifest.json" not in upload_paths:
        upload_paths.insert(0, "manifest.json")
    for rel in upload_paths:
        zenodo_upload_file(dep_id, str(rd / rel), token=tok, sandbox=sandbox, timeout=timeout)

    if publish:
        return zenodo_publish(dep_id, token=tok, sandbox=sandbox, timeout=timeout)
    return deposition


# ---------------------------------------------------------------------------
# Software Heritage
# ---------------------------------------------------------------------------

_SWH_API_BASE = "https://archive.softwareheritage.org/api/1"


class SoftwareHeritageError(Exception):
    """Raised when a Software Heritage API call fails."""


def swh_resolve_git_sha(
    git_sha: str,
    *,
    timeout: int = 15,
) -> Optional[str]:
    """Return the SWH persistent identifier for a git commit SHA, or None.

    Queries the public Software Heritage API without authentication.  If the
    commit has not been archived yet, returns ``None`` rather than raising.

    Parameters
    ----------
    git_sha:
        Full 40-character hex git commit SHA to look up.  Abbreviated SHAs
        are rejected with :exc:`ValueError` because SWH identifiers require
        the complete SHA1.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    str or None
        A ``swh:1:rev:<sha1>`` identifier, or ``None`` if not found.

    Raises
    ------
    ValueError
        If *git_sha* is not a full 40-character hex string.
    SoftwareHeritageError
        On network or unexpected API errors.
    """
    if len(git_sha) != 40 or not all(c in "0123456789abcdefABCDEF" for c in git_sha):
        raise ValueError(
            f"swh_resolve_git_sha requires a full 40-character hex SHA; got {git_sha!r}"
        )
    url = f"{_SWH_API_BASE}/resolve/swh:1:rev:{git_sha}/"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            # A non-empty response means the commit is archived.  Build the
            # SWH identifier from the queried git_sha rather than
            # data["object_id"], which is an internal SWH field that may
            # differ from the git SHA in future API versions.
            if data.get("object_id") or data.get("object_type"):
                return f"swh:1:rev:{git_sha}"
            return None
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise SoftwareHeritageError(
            f"Software Heritage API error {exc.code}"
        ) from exc
    except URLError as exc:
        raise SoftwareHeritageError(
            f"Software Heritage request failed: {exc.reason}"
        ) from exc


def swh_lookup(
    manifest: "Manifest",
    *,
    timeout: int = 15,
) -> Optional[str]:
    """Return the SWH persistent identifier for the git commit in *manifest*.

    Parameters
    ----------
    manifest:
        A built manifest with a ``git_sha`` field.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    str or None
        SWH identifier, or ``None`` if the SHA is absent or not archived.
    """
    if not manifest.git_sha:
        return None
    sha = manifest.git_sha
    if len(sha) != 40 or not all(c in "0123456789abcdefABCDEF" for c in sha):
        return None
    return swh_resolve_git_sha(sha, timeout=timeout)
