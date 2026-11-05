"""Vercel function: POST /api/translate (the text of an article, paragraph by paragraph, into Chinese or English) and GET /api/translate (is it set up). All the logic is in translateapi.py (one directory up).
The keys of the providers are environment variables of this project (DEEPL_API_KEY, ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET); nothing is logged here: the text is a reader's reading."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import translateapi  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        translateapi.respond(self, os.environ, 'OPTIONS')

    def do_GET(self):
        translateapi.respond(self, os.environ, 'GET')

    def do_POST(self):
        translateapi.respond(self, os.environ, 'POST')

    def log_message(self, *args):                 # no access log lines
        pass
