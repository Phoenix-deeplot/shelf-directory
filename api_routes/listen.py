"""Vercel function: GET /api/listen -> today's listening (scenes of songs and play lists, different every day). All the logic is in listen.py (one directory up). Stores nothing."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import listen  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        listen.respond(self, os.environ, 'GET')

    def do_POST(self):
        listen.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who asked for what is kept here
        pass
