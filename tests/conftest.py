import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

_GITHUB_ENV_VARS = [
    "GITHUB_ACTIONS",
    "GITHUB_SHA",
    "GITHUB_REPOSITORY",
    "GITHUB_REF",
    "GITHUB_WORKFLOW",
    "GITHUB_RUN_ID",
    "GITHUB_SERVER_URL",
]


@pytest.fixture(autouse=True)
def _clear_github_env(monkeypatch):
    """Clear GitHub Actions env vars so tests using fake git SHAs don't trip
    the ci_provenance check when the test suite itself runs inside CI."""
    for var in _GITHUB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class StubServer:
    """Minimal HTTP stub: records requests, returns canned responses.

    response_body is used for POST requests; get_responses maps a URL path to
    the body returned for GET requests (404 for unknown paths).
    """

    def __init__(self):
        self.requests = []
        self.response_body = b"{}"
        self.response_status = 200
        self.get_responses = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self, body=b""):
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )

            def _respond(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):
                self._record()
                body = outer.get_responses.get(self.path)
                status = 200 if body is not None else 404
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                # HEAD responses must not include a body.

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self._record(self.rfile.read(length))
                self._respond(outer.response_status, outer.response_body)

            def do_GET(self):
                self._record()
                body = outer.get_responses.get(self.path)
                if body is None:
                    self._respond(404, b"not found")
                else:
                    self._respond(200, body)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub_server():
    server = StubServer()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _suppress_ci_env(monkeypatch):
    """Remove GitHub Actions env vars so tests never trigger CI provenance
    auto-detection unless they explicitly set those vars themselves."""
    for var in (
        "GITHUB_ACTIONS",
        "GITHUB_SHA",
        "GITHUB_REPOSITORY",
        "GITHUB_REF",
        "GITHUB_WORKFLOW",
        "GITHUB_RUN_ID",
        "GITHUB_SERVER_URL",
    ):
        monkeypatch.delenv(var, raising=False)
