"""Vercel function: POST /api/feed (what is new in the feeds a reader subscribes to). All the logic is in feedapi.py (one directory up). Stores nothing."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import feedapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        feedapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        feedapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        feedapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who asked for what is kept here
        pass
