"""GET /api/analytics/config?experiment=<key>."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyticsapi  # noqa: E402
from submission import Reply  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, status, body, origin=""):
        raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Robots-Tag", "noindex")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin); self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        try:
            if origin and not analyticsapi._allowed_origin(origin, os.environ):
                raise Reply(403, "origin", "这个来源不允许", "Origin is not allowed")
            query = parse_qs(urlsplit(self.path).query)
            body = analyticsapi.public_config((query.get("experiment") or [""])[0], os.environ)
            self._send(200, body, origin if analyticsapi._allowed_origin(origin, os.environ) else "")
        except Reply as reply:
            self._send(reply.status, reply.body(), origin if analyticsapi._allowed_origin(origin, os.environ) else "")
        except Exception:
            self._send(503, {"ok": False, "error": "database", "message": "分析服务暂不可用"})

    def do_OPTIONS(self):
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        if not analyticsapi._allowed_origin(origin, os.environ):
            return self._send(403, {"ok": False, "error": "origin", "message": "这个来源不允许"})
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", origin); self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0"); self.end_headers()

    def log_message(self, *args):
        pass

