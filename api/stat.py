"""Vercel function: POST /api/stat (anonymous counters: opened / saved / subscribed) and GET /api/stat (the busiest entries). All the logic is in statapi.py (one directory up).
What it keeps is a number per entry and day, nothing about who."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import statapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        statapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        statapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        statapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who counted what is kept here
        pass
