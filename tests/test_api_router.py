"""The single Vercel entrypoint preserves the existing API routing contract."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
import router  # noqa: E402


class RequestHarness:
    def __init__(self, path, command='GET'):
        self.path = path
        self.command = command
        self.errors = []

    def send_error(self, code, message=''):
        self.errors.append((code, message))


class TestRouterPath(unittest.TestCase):
    def test_rewrite_restores_public_path_and_preserves_regular_query_values(self):
        target = router._rewritten_target(
            '/api/router?__route=hot/events/refresh&lang=en&tag=a&tag=b&empty='
        )
        self.assertEqual(target, '/api/hot/events/refresh?lang=en&tag=a&tag=b&empty=')

    def test_missing_or_unrecognized_route_is_not_imported(self):
        self.assertIsNone(router._rewritten_target('/api/router'))
        self.assertIsNone(router._rewritten_target('/api/router?__route=adminapi'))

    def test_route_allowlist_covers_all_legacy_api_paths(self):
        self.assertEqual(len(router.ROUTES), 13)
        self.assertEqual(set(router.ROUTES), {
            '/api/admin', '/api/book', '/api/feed', '/api/feedback', '/api/hot',
            '/api/hot/events', '/api/hot/events/refresh', '/api/listen', '/api/ops',
            '/api/stat', '/api/submit', '/api/sync', '/api/translate',
        })

    def test_dispatch_calls_legacy_handler_with_reconstructed_request_path(self):
        observed = []

        class Endpoint:
            @staticmethod
            def do_GET(request):
                observed.append((request.path, request.command))

        request = RequestHarness('/api/router?__route=hot&format=json&p=weibo')
        with patch.dict(router.ROUTES, {'/api/hot': Endpoint}):
            router.dispatch(request)
        self.assertEqual(observed, [('/api/hot?format=json&p=weibo', 'GET')])
        self.assertEqual(request.errors, [])

    def test_dispatch_rejects_unknown_route_before_import(self):
        request = RequestHarness('/api/router?__route=not-a-route')
        with patch.dict(router.ROUTES, {}, clear=True):
            router.dispatch(request)
        self.assertEqual(request.errors[0][0], 404)

    def test_dispatch_preserves_endpoint_method_not_allowed_behavior(self):
        class Endpoint:
            @staticmethod
            def do_GET(request):
                raise AssertionError('POST must not be routed to GET')

        request = RequestHarness('/api/router?__route=book', 'POST')
        with patch.dict(router.ROUTES, {'/api/book': Endpoint}):
            router.dispatch(request)
        self.assertEqual(request.errors[0][0], 501)

    def test_vercel_rewrite_is_single_wildcard_route(self):
        config = json.loads((ROOT / 'vercel.json').read_text(encoding='utf-8'))
        self.assertEqual(config['rewrites'], [{
            'source': '/api/:path*',
            'destination': '/api/router?__route=:path*',
        }])

    def test_all_legacy_handler_modules_import_outside_function_directory(self):
        for path, endpoint in router.ROUTES.items():
            with self.subTest(path=path):
                self.assertTrue(issubclass(endpoint, router.BaseHTTPRequestHandler))

    def test_api_directory_contains_exactly_one_python_function_entry(self):
        python_files = sorted((ROOT / 'api').rglob('*.py'))
        self.assertEqual(python_files, [ROOT / 'api' / 'router.py'])


if __name__ == '__main__':
    unittest.main()
