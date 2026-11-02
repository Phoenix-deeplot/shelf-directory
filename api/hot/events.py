"""Vercel function: GET /api/hot/events?lang=zh&limit=12."""
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import hotapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, content_type, body, cache = hotapi.handle_events(
            urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query), os.environ)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', cache)
        self.send_header('X-Robots-Tag', 'noindex')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_response(405)
        self.send_header('Allow', 'GET')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        pass
