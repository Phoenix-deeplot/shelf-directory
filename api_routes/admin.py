"""Vercel function: /api/admin (password sign-in for the admin page). All the logic is in adminapi.py (one directory up)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import adminapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        adminapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        adminapi.respond(self, os.environ, 'POST')

    def do_OPTIONS(self):                         # no CORS: the page is on the same site, so nothing from another origin is ever allowed
        adminapi.respond(self, os.environ, 'OPTIONS')

    def log_message(self, *args):
        pass
