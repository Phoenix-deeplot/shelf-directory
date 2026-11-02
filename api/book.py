"""Vercel function: GET /api/book?b=<book>&c=<chapter> (a chapter of a picked book: a copy that is kept for everybody, or read from the original page once). All the logic is in bookapi.py (one directory up).
Redis (UPSTASH_REDIS_REST_URL / _TOKEN or KV_REST_API_URL / _TOKEN) and PostgreSQL (OPS_DATABASE_URL or POSTGRES_URL) are optional layers; see bookapi.py."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import bookapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        bookapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        bookapi.respond(self, os.environ, 'GET')

    def log_message(self, *args):                 # no access log lines: nothing about who read what is kept here
        pass
