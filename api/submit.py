"""Vercel function: POST /api/submit. All the logic is in submission.py (one directory up)."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import submission  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        submission.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        submission.respond(self, os.environ, 'GET')

    def do_POST(self):
        submission.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: the sender's address is not kept anywhere here
        pass
