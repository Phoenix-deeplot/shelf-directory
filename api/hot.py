"""Vercel function: GET /api/hot?p=github -> the platform's current hot list. If PostgreSQL is configured, ranking metadata snapshots are retained for the seven-day view. Logic is in hotapi.py."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hotapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        hotapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        hotapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who asked for what is kept here
        pass
