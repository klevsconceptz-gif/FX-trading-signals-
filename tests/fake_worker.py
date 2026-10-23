"""In-process stand-in for the Cloudflare Worker's internal routes.

It implements the contract that ``fxbot.replica`` and ``fxbot.mail`` rely on:

    GET  /__internal/snapshot   200 snapshot bytes | 404 none yet | 401 bad token
    PUT  /__internal/snapshot   204 stored | 413 too large | 401 bad token
    POST /__internal/email      202 accepted | 400 bad input | 502 send failed | 401 bad token

The real Worker is in ``edge/`` and is checked by its own tests (``npm test`` there).
This fake keeps the Python side testable offline.
"""

from __future__ import annotations

import hmac
import http.server
import json
import threading
from typing import Any

MAX_SNAPSHOT = 256 * 1024 * 1024


class FakeWorker:
    def __init__(self, token: str = "test-internal-token") -> None:
        self.token = token
        self.snapshot: bytes | None = None
        self.emails: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []   # (method, path) for every call
        self.fail_puts = 0          # next N snapshot PUTs answer 500
        self.fail_email = False     # answer 502 to e-mail posts
        self._server: http.server.ThreadingHTTPServer | None = None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> str:
        worker = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output quiet
                return

            def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _authorised(self) -> bool:
                sent = self.headers.get("x-internal-token", "")
                return hmac.compare_digest(sent.encode(), worker.token.encode())

            def do_GET(self) -> None:
                worker.requests.append(("GET", self.path))
                if not self._authorised():
                    return self._send(401)
                if self.path != "/__internal/snapshot":
                    return self._send(404)
                if worker.snapshot is None:
                    return self._send(404)
                return self._send(200, worker.snapshot, "application/octet-stream")

            def do_PUT(self) -> None:
                worker.requests.append(("PUT", self.path))
                if not self._authorised():
                    return self._send(401)
                if self.path != "/__internal/snapshot":
                    return self._send(404)
                length = int(self.headers.get("content-length", "0"))
                if length > MAX_SNAPSHOT:
                    return self._send(413)
                body = self.rfile.read(length)
                if worker.fail_puts > 0:
                    worker.fail_puts -= 1
                    return self._send(500)
                worker.snapshot = body
                return self._send(204)

            def do_POST(self) -> None:
                worker.requests.append(("POST", self.path))
                if not self._authorised():
                    return self._send(401)
                if self.path != "/__internal/email":
                    return self._send(404)
                length = int(self.headers.get("content-length", "0"))
                try:
                    message = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400)
                if not isinstance(message, dict) or not all(
                    isinstance(message.get(k), str) and message.get(k) for k in ("to", "subject", "text")
                ):
                    return self._send(400)
                if worker.fail_email:
                    return self._send(502)
                worker.emails.append(message)
                return self._send(202)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
