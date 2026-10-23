"""Vercel function: GET /api/hot?p=github -> an RSS feed of that platform's hot list right now (GET /api/hot lists what is on). All the logic is in hotapi.py (one directory up). Stores nothing."""
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
