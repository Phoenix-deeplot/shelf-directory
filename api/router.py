"""Single Vercel function that dispatches the existing public API paths."""
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_routes import admin, book, feed, feedback, hot, hot_events, hot_events_refresh, listen, ops, stat, submit, sync, translate  # noqa: E402

ROUTES = {
    '/api/admin': admin.handler,
    '/api/book': book.handler,
    '/api/feed': feed.handler,
    '/api/feedback': feedback.handler,
    '/api/hot': hot.handler,
    '/api/hot/events': hot_events.handler,
    '/api/hot/events/refresh': hot_events_refresh.handler,
    '/api/listen': listen.handler,
    '/api/ops': ops.handler,
    '/api/stat': stat.handler,
    '/api/submit': submit.handler,
    '/api/sync': sync.handler,
    '/api/translate': translate.handler,
}


def _rewritten_target(target):
    """Restore the public path from Vercel's ``__route`` rewrite parameter."""
    parsed = urllib.parse.urlsplit(target)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    paths = params.pop('__route', [])
    if not paths:
        return None
    route = '/api/' + str(paths[0]).strip('/')
    if route not in ROUTES:
        return None
    query = urllib.parse.urlencode(params, doseq=True)
    return route + (('?' + query) if query else '')


def dispatch(request_handler):
    """Dispatch through an allowlist while reusing each endpoint's old handler."""
    public_target = _rewritten_target(request_handler.path)
    if public_target is None:
        request_handler.send_error(404, 'Not found')
        return

    public_path = urllib.parse.urlsplit(public_target).path
    endpoint = ROUTES[public_path]
    method = getattr(endpoint, 'do_' + request_handler.command, None)
    if method is None:
        request_handler.send_error(501, 'Unsupported method')
        return

    # Existing handlers parse their original path/query and use this request's
    # socket, headers, body reader, and response writer directly.
    request_handler.path = public_target
    method(request_handler)


class handler(BaseHTTPRequestHandler):
    def _dispatch(self):
        dispatch(self)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def log_message(self, *args):
        # Preserve the existing handlers' privacy posture: don't log requests.
        pass
