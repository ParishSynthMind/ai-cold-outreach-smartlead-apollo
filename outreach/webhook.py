"""A tiny webhook receiver for Smartlead (standard library only).

Smartlead calls the URL you register; this checks a shared token in the URL with a
constant-time compare, caps the body size, hands the event to replies.handle_event and
answers 200 straight away. Run it behind HTTPS (a reverse proxy or a tunnel) in real use.
"""
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .replies import handle_event

log = logging.getLogger(__name__)
MAX_BODY = 256 * 1024


def make_handler(token, store, provider, client):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802 (http.server naming)
            url = urlparse(self.path)
            given = (parse_qs(url.query).get("token") or [""])[0]
            if url.path != "/smartlead" or not hmac.compare_digest(given.encode(), token.encode()):
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "bad size"})
            try:
                payload = json.loads(self.rfile.read(length))
            except ValueError:
                return self._send(400, {"error": "not json"})
            if not isinstance(payload, dict):
                return self._send(400, {"error": "not an object"})
            try:
                result = handle_event(payload, store, provider, client)
            except Exception:  # never let one bad event kill the receiver
                log.exception("event failed")
                return self._send(500, {"error": "handler failed"})
            return self._send(200, result)

        def log_message(self, fmt, *args):  # keep tokens out of the access log
            log.info("webhook %s", self.command)

    return Handler


def serve(host, port, token, store, provider, client):
    if len(token) < 16:
        raise ValueError("webhook token must be at least 16 characters")
    server = ThreadingHTTPServer((host, port), make_handler(token, store, provider, client))
    log.info("listening on %s:%s/smartlead", host, port)
    return server
