"""Tests for farm_notary.archive — Zenodo and Software Heritage helpers."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from farm_notary.archive import (
    SoftwareHeritageError,
    ZenodoError,
    deposit_manifest,
    swh_lookup,
    swh_resolve_git_sha,
    zenodo_create_deposit,
    zenodo_publish,
    zenodo_upload_file,
)
from farm_notary.manifest import build_manifest


def _make_manifest(tmp_path: Path):
    (tmp_path / "output.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return build_manifest(
        tmp_path,
        publish_patterns=["*.csv"],
        git_sha="deadbeef",
        config={"lr": 0.01},
    )


# ---------------------------------------------------------------------------
# Stub HTTP server helper
# ---------------------------------------------------------------------------


class _StubServer:
    def __init__(self, responses: dict):
        self._responses = responses
        self._requests: list = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # suppress output
                pass

            def _respond(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                outer._requests.append({"method": "POST", "path": self.path, "body": body})
                resp = outer._responses.get(self.path, {})
                code = resp.get("status", 200)
                data = json.dumps(resp.get("body", {})).encode()
                self._respond(code, data)

            def do_GET(self):
                outer._requests.append({"method": "GET", "path": self.path})
                resp = outer._responses.get(self.path, {})
                code = resp.get("status", 200)
                data = json.dumps(resp.get("body", {})).encode()
                self._respond(code, data)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def shutdown(self):
        self._server.shutdown()


# ---------------------------------------------------------------------------
# Zenodo tests (stub server)
# ---------------------------------------------------------------------------


class TestZenodoCreateDeposit:
    def test_calls_depositions_endpoint(self, tmp_path):
        stub = _StubServer(
            {
                "/api/deposit/depositions": {"status": 201, "body": {"id": 123, "links": {"html": "http://z/123"}}}
            }
        )
        try:
            with patch("farm_notary.archive._ZENODO_API_BASE", stub.base_url + "/api"):
                result = zenodo_create_deposit(token="tok", timeout=5)
            assert result["id"] == 123
        finally:
            stub.shutdown()

    def test_http_error_raises_zenodo_error(self, tmp_path):
        stub = _StubServer({"/api/deposit/depositions": {"status": 403, "body": {"message": "forbidden"}}})
        try:
            with patch("farm_notary.archive._ZENODO_API_BASE", stub.base_url + "/api"):
                with pytest.raises(ZenodoError):
                    zenodo_create_deposit(token="bad", timeout=5)
        finally:
            stub.shutdown()


class TestDepositManifest:
    def test_no_token_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZENODO_TOKEN", raising=False)
        m = _make_manifest(tmp_path)
        with pytest.raises(ValueError, match="token"):
            deposit_manifest(m, str(tmp_path), token="", timeout=5)

    def test_uploads_manifest_json(self, tmp_path, monkeypatch):
        from farm_notary.manifest import write_manifest

        monkeypatch.delenv("ZENODO_TOKEN", raising=False)
        m = _make_manifest(tmp_path)
        write_manifest(m, tmp_path)

        stub = _StubServer(
            {
                "/api/deposit/depositions": {"status": 201, "body": {"id": 42, "links": {}}},
                "/api/deposit/depositions/42/files": {"status": 201, "body": {"id": "f1"}},
            }
        )
        try:
            with patch("farm_notary.archive._ZENODO_API_BASE", stub.base_url + "/api"):
                result = deposit_manifest(
                m, str(tmp_path), token="tok", timeout=5,
                metadata={"creators": [{"name": "Test Author"}]},
            )
            assert result["id"] == 42
            # One POST to /depositions and one to /depositions/42/files
            paths = [r["path"] for r in stub._requests]
            assert any("/files" in p for p in paths)
        finally:
            stub.shutdown()


# ---------------------------------------------------------------------------
# Software Heritage tests
# ---------------------------------------------------------------------------


class TestSWHResolve:
    def test_found_returns_swh_id(self, tmp_path):
        sha = "a" * 40
        stub = _StubServer(
            {
                f"/api/1/resolve/swh:1:rev:{sha}/": {
                    "status": 200,
                    "body": {"object_id": sha, "object_type": "revision"},
                }
            }
        )
        try:
            with patch("farm_notary.archive._SWH_API_BASE", stub.base_url + "/api/1"):
                result = swh_resolve_git_sha(sha, timeout=5)
            assert result == f"swh:1:rev:{sha}"
        finally:
            stub.shutdown()

    def test_not_found_returns_none(self, tmp_path):
        sha = "b" * 40
        stub = _StubServer(
            {f"/api/1/resolve/swh:1:rev:{sha}/": {"status": 404, "body": {}}}
        )
        try:
            with patch("farm_notary.archive._SWH_API_BASE", stub.base_url + "/api/1"):
                result = swh_resolve_git_sha(sha, timeout=5)
            assert result is None
        finally:
            stub.shutdown()

    def test_server_error_raises(self, tmp_path):
        sha = "c" * 40
        stub = _StubServer(
            {f"/api/1/resolve/swh:1:rev:{sha}/": {"status": 500, "body": {}}}
        )
        try:
            with patch("farm_notary.archive._SWH_API_BASE", stub.base_url + "/api/1"):
                with pytest.raises(SoftwareHeritageError):
                    swh_resolve_git_sha(sha, timeout=5)
        finally:
            stub.shutdown()


class TestSWHLookup:
    def test_no_git_sha_returns_none(self, tmp_path):
        m = _make_manifest(tmp_path)
        object.__setattr__(m, "git_sha", None)
        assert swh_lookup(m) is None
