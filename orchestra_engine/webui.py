"""The live view's server: one page for every run, shown the way an AI editor
shows a chat — thinking, messages and tool calls arriving in order, runs and
stages down the side, prompts answered with a button.

It reads nothing a run's channel doesn't already hold. Which runs exist comes
from the index in viewer.py, a run's events from its `events.jsonl`, and an
answer goes through the same `channel.answer` that `--answer` uses, so a reply
from the page is refused for exactly the reasons a reply from the command line
is.

The page can approve a permission prompt, so who reaches it matters:
- bound to 127.0.0.1 only, on a random port;
- every request carries a random token minted at start (in the URL the engine
  opens, then as a header), compared in constant time;
- the Host header must name this server, so a page on another origin cannot
  reach it by rebinding a hostname to 127.0.0.1;
- answers are JSON-only and capped in size, and no CORS headers are sent, so
  a cross-origin page cannot post one;
- a run is named by id and its directory read from the index, never taken
  from the request.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import channel, viewer

TOKEN_HEADER = "X-Orchestra-Token"
MAX_BODY_BYTES = 64 * 1024


VENDOR_SCRIPTS = ("marked.umd.js", "purify.min.js")


def _page() -> bytes:
    # The CSP allows no external hosts, so the Markdown libraries ride inline.
    pkg = resources.files("orchestra_engine")
    scripts = b"".join(
        b"<script>\n" + pkg.joinpath("vendor", name).read_bytes() + b"\n</script>\n"
        for name in VENDOR_SCRIPTS
    )
    return pkg.joinpath("webui.html").read_bytes().replace(b"<!--VENDOR-->", scripts, 1)


class _Handler(BaseHTTPRequestHandler):
    server: "LiveView"  # type: ignore[assignment]

    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _allowed(self, token: str | None) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        return token is not None and hmac.compare_digest(token, self.server.token)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            if not self._allowed((query.get("t") or [None])[0]):
                return self._send(403, b"forbidden", "text/plain")
            return self._send(200, _page(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        if not self._allowed(self.headers.get(TOKEN_HEADER)):
            return self._json(403, {"error": "forbidden"})
        if url.path == "/api/ping":
            return self._json(200, {"ok": True, "page_open": self.server.page_open()})
        if url.path == "/api/runs":
            self.server.last_page_poll = time.monotonic()
            return self._json(200, {"runs": viewer.list_runs()})
        if url.path == "/api/events":
            directory = viewer.run_dir((query.get("run") or [""])[0])
            if directory is None:
                return self._json(404, {"error": "no such run"})
            try:
                start = max(0, int((query.get("from") or ["0"])[0]))
            except ValueError:
                start = 0
            events = channel.read_events(directory)
            return self._json(200, {"events": events[start:], "next": len(events)})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/answer":
            return self._send(404, b"not found", "text/plain")
        if not self._allowed(self.headers.get(TOKEN_HEADER)):
            return self._json(403, {"error": "forbidden"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "JSON only"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            return self._json(413, {"error": "body too large or empty"})
        try:
            body = json.loads(self.rfile.read(length))
            run_id, prompt_id, value = str(body["run"]), str(body["id"]), str(body["value"])
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "expected {run, id, value}"})
        directory = viewer.run_dir(run_id)
        if directory is None:
            return self._json(404, {"error": "no such run"})
        try:
            channel.answer(directory, prompt_id, value)
        except channel.ChannelError as exc:
            return self._json(409, {"error": str(exc)})
        self._json(200, {"ok": True})


class LiveView(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0):
        super().__init__(("127.0.0.1", port), _Handler)
        self.token = secrets.token_urlsafe(32)
        self.last_page_poll = float("-inf")

    def page_open(self) -> bool:
        return time.monotonic() - self.last_page_poll < viewer.PAGE_OPEN_SECONDS
