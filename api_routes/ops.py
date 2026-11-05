"""Vercel function: /api/ops (private operations data for /admin/)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import opsapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        opsapi.respond(self, os.environ, "GET")

    def do_POST(self):
        opsapi.respond(self, os.environ, "POST")

    def do_OPTIONS(self):
        opsapi.respond(self, os.environ, "OPTIONS")

    def log_message(self, *args):
        pass
