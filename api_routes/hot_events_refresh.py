"""Authenticated Vercel Cron endpoint: /api/hot/events/refresh."""
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hotapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _run(self, method):
        status, content_type, body, cache = hotapi.handle_event_refresh(
            method,
            self.headers,
            os.environ,
            urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query),
        )
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', cache)
        self.send_header('X-Robots-Tag', 'noindex')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._run('GET')

    def do_POST(self):
        self._run('POST')

    def log_message(self, *args):
        pass
