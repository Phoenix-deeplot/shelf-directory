"""POST /api/analytics/events."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
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

    def do_POST(self):
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        if not analyticsapi._allowed_origin(origin, os.environ):
            return self._send(403, {"ok": False, "error": "origin", "message": "这个来源不允许"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > analyticsapi._MAX_BODY:
            return self._send(413, {"ok": False, "error": "size", "message": "内容太大"}, origin)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "request", "message": "JSON 格式不正确"}, origin)
        source = (self.headers.get("X-Vercel-Forwarded-For") or self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        if not source and self.client_address:
            source = self.client_address[0]
        try:
            if urlsplit(self.path).path != "/api/analytics/events":
                raise Reply(404, "route", "找不到分析接口", "Analytics endpoint not found")
            body = analyticsapi.ingest_events(payload, os.environ, source)
            self._send(200, body, origin)
        except Reply as reply:
            self._send(reply.status, reply.body(), origin)
        except Exception:
            self._send(503, {"ok": False, "error": "database", "message": "分析服务暂不可用"}, origin)

    def do_OPTIONS(self):
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        if not analyticsapi._allowed_origin(origin, os.environ):
            return self._send(403, {"ok": False, "error": "origin", "message": "这个来源不允许"})
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", origin); self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0"); self.end_headers()

    def log_message(self, *args):
        pass

