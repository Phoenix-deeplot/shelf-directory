"""Vercel function: POST /api/sync (the optional sync code). All the logic is in syncapi.py (one directory up). What it keeps is encrypted by the reader's browser; see there."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import syncapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        syncapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        syncapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        syncapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who synced what is kept here
        pass
