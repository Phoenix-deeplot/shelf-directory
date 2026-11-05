"""Vercel function: POST /api/feedback (a few words from a reader to the maintainer, anonymous) and GET /api/feedback ({"configured": bool}). All the logic is in feedbackapi.py (one directory up).
What it keeps is the words, the kind, an optional contact the reader wrote, the page name, the language, the version and the day; nothing about who."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import feedbackapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        feedbackapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        feedbackapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        feedbackapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines: nothing about who wrote what is kept here
        pass
