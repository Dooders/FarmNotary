"""Content-addressed upload via the Kubo (go-ipfs) HTTP API.

Talks to a local or remote daemon's ``/api/v0/add`` endpoint using only the
standard library. The run directory is uploaded wrapped in a directory so the
returned root CID resolves to the artifact tree, manifest.json included.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_API_URL = "http://127.0.0.1:5001"
DEFAULT_GATEWAY_URL = "https://ipfs.io"
_GATEWAY_TIMEOUT = 15.0


class IpfsError(RuntimeError):
    pass


def _multipart_body(files: List[Tuple[str, bytes]]) -> Tuple[bytes, str]:
    boundary = "farm-notary-" + uuid.uuid4().hex
    parts = []
    for rel_path, data in files:
        quoted = urllib.parse.quote(rel_path, safe="")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{quoted}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


class IpfsClient:
    def __init__(self, api_url: str = None, timeout: float = 120.0):
        self.api_url = (
            api_url
            or os.environ.get("FARM_NOTARY_IPFS_API")
            or DEFAULT_API_URL
        ).rstrip("/")
        self.timeout = timeout

    def add_files(self, files: List[Tuple[str, bytes]]) -> str:
        """Upload (relative_path, content) pairs; return the root directory CID."""
        if not files:
            raise IpfsError("nothing to upload")
        body, boundary = _multipart_body(files)
        query = urllib.parse.urlencode(
            {"wrap-with-directory": "true", "cid-version": "1", "pin": "true"}
        )
        request = urllib.request.Request(
            f"{self.api_url}/api/v0/add?{query}",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except OSError as exc:
            raise IpfsError(
                f"IPFS API at {self.api_url} unreachable: {exc}. "
                "Is a Kubo daemon running, or set FARM_NOTARY_IPFS_API?"
            ) from exc
        entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not entries:
            raise IpfsError("IPFS add returned no entries")
        # The wrapping directory is emitted last (its Name is empty).
        root = next((e for e in reversed(entries) if e.get("Name", "") == ""), entries[-1])
        cid = root.get("Hash")
        if not cid:
            raise IpfsError(f"IPFS add response had no root CID: {entries!r}")
        return cid

    def add_run_dir(self, run_dir: Path, names: Iterable[str]) -> str:
        """Upload the named files (paths relative to run_dir); return root CID."""
        run_dir = Path(run_dir)
        files = []
        for name in names:
            path = run_dir / name
            files.append((name, path.read_bytes()))
        return self.add_files(files)

    def pin_remote(self, cid: str, service: str, name: Optional[str] = None) -> dict:
        """Delegate pinning to a remote service via Kubo's remote-pin API.

        Calls ``/api/v0/pin/remote/add`` on the local Kubo daemon, which
        forwards the request to *service* (a name registered with
        ``ipfs pin remote service add``).  Returns the parsed JSON response.
        """
        params: dict = {"arg": cid, "service": service}
        if name:
            params["name"] = name
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.api_url}/api/v0/pin/remote/add?{query}",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            raise IpfsError(
                f"IPFS remote-pin via {self.api_url} failed: {exc}"
            ) from exc


def check_gateway_reachability(
    cid: str,
    gateway_url: Optional[str] = None,
    timeout: float = _GATEWAY_TIMEOUT,
) -> bool:
    """Return True if *cid* is resolvable through *gateway_url*.

    Issues a HEAD request to ``{gateway_url}/ipfs/{cid}`` so that no data is
    transferred; a 2xx response is treated as reachable.  Network errors and
    non-2xx responses both return False.
    """
    base = (gateway_url or DEFAULT_GATEWAY_URL).rstrip("/")
    url = f"{base}/ipfs/{cid}"
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
