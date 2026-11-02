"""The translation of an article's text (translate.py: translate_texts, translateapi.py): the providers, the signature of Aliyun's request, the allowances, the cache, the HTTP side.
Every provider's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import syncapi as sa  # noqa: E402
import translate  # noqa: E402
import translateapi as ta  # noqa: E402
from submission import Reply  # noqa: E402
from test_stat import FakeHandler  # noqa: E402

T0 = 1_792_000_000.0
BOTH = {'DEEPL_API_KEY': 'abc:fx', 'ALIYUN_ACCESS_KEY_ID': 'id1', 'ALIYUN_ACCESS_KEY_SECRET': 'sec1', 'IP_SALT': 'salt', 'ALLOWED_ORIGINS': 'https://books.zhitree.cn'}
ONLY_DEEPL = {k: v for k, v in BOTH.items() if not k.startswith('ALIYUN')}


def providers_that(calls, deepl=None, aliyun=None):
    """Fake providers: each answers 'zh:' + the text unless it is an exception to raise."""
    def make(name, behaviour):
        def run(texts, to, src, env):
            calls.append((name, list(texts), to, src))
            if isinstance(behaviour, Exception):
                raise behaviour
            return [name + ':' + t for t in texts]
        return run
    return {'deepl': make('deepl', deepl), 'aliyun': make('aliyun', aliyun)}


class TestProviders(unittest.TestCase):
    def test_which_providers_and_in_which_order(self):
        self.assertEqual(translate.configured({}), [])
        self.assertEqual(translate.configured({'DEEPL_API_KEY': ' '}), [])
        self.assertEqual(translate.configured({'ALIYUN_ACCESS_KEY_ID': 'x'}), [])                         # (an id without its secret is nothing)
        self.assertEqual(translate.configured(BOTH), ['deepl', 'aliyun'])
        self.assertEqual(translate.configured({**BOTH, 'TRANSLATE_PROVIDER': 'aliyun'}), ['aliyun', 'deepl'])
        self.assertEqual(translate.configured({**ONLY_DEEPL, 'TRANSLATE_PROVIDER': 'aliyun'}), ['deepl'])
        self.assertEqual(translate.provider({'ALIYUN_ACCESS_KEY_ID': 'i', 'ALIYUN_ACCESS_KEY_SECRET': 's'}), 'aliyun')    # (the titles too may use it)

    def test_the_first_provider_answers_and_the_second_is_not_asked(self):
        calls = []
        name, out = translate.translate_texts(['Hello', 'World'], 'zh', 'auto', BOTH, providers_that(calls))
        self.assertEqual((name, out, [c[0] for c in calls]), ('deepl', ['deepl:Hello', 'deepl:World'], ['deepl']))

    def test_a_provider_without_allowance_or_without_an_answer_gives_way_to_the_next(self):
        for why in (translate.HTTPFail(456), translate.HTTPFail(429), translate.HTTPFail(500), OSError('down'), ValueError('odd'), KeyError('translations')):
            calls = []
            name, out = translate.translate_texts(['Hi'], 'zh', 'en', BOTH, providers_that(calls, deepl=why))
            self.assertEqual((name, out, [c[0] for c in calls]), ('aliyun', ['aliyun:Hi'], ['deepl', 'aliyun']), repr(why))

    def test_when_every_provider_fails_the_reason_is_told(self):
        with self.assertRaises(translate.Unavailable) as cm:
            translate.translate_texts(['Hi'], 'zh', 'en', BOTH, providers_that([], deepl=translate.HTTPFail(456), aliyun=translate.HTTPFail(429)))
        self.assertEqual(cm.exception.code, 'quota')
        with self.assertRaises(translate.Unavailable) as cm:
            translate.translate_texts(['Hi'], 'zh', 'en', BOTH, providers_that([], deepl=OSError('x'), aliyun=translate.HTTPFail(429)))
        self.assertEqual(cm.exception.code, 'quota')                                                       # (the last word: one of them has none left)
        with self.assertRaises(translate.Unavailable) as cm:
            translate.translate_texts(['Hi'], 'zh', 'en', BOTH, providers_that([], deepl=OSError('x'), aliyun=OSError('y')))
        self.assertEqual(cm.exception.code, 'fail')
        with self.assertRaises(translate.Unavailable) as cm:
            translate.translate_texts(['Hi'], 'zh', 'en', {})
        self.assertEqual(cm.exception.code, 'nokey')

    def test_an_answer_with_another_number_of_texts_is_no_answer(self):
        calls = []
        bad = {'deepl': lambda t, to, s, e: ['only one'], 'aliyun': providers_that(calls)['aliyun']}
        name, out = translate.translate_texts(['a', 'b'], 'zh', 'en', BOTH, bad)
        self.assertEqual((name, out), ('aliyun', ['aliyun:a', 'aliyun:b']))

    def test_deepl_gets_the_target_and_the_source_language_and_the_key_in_a_header(self):
        seen = []

        def post(url, payload, headers):
            seen.append((url, payload, headers))
            return {'translations': [{'text': '译' + t} for t in payload['text']]}
        orig, translate._post = translate._post, post
        try:
            self.assertEqual(translate.deepl_texts(['a', 'b'], 'zh', 'en', ONLY_DEEPL), ['译a', '译b'])
            translate.deepl_texts(['a'], 'en', 'auto', {'DEEPL_API_KEY': 'paid-key'})
        finally:
            translate._post = orig
        self.assertEqual((seen[0][0], seen[0][1]['target_lang'], seen[0][1]['source_lang']), ('https://api-free.deepl.com/v2/translate', 'ZH', 'EN'))
        self.assertEqual(seen[0][2]['Authorization'], 'DeepL-Auth-Key abc:fx')
        self.assertEqual((seen[1][0], seen[1][1]['target_lang'], 'source_lang' in seen[1][1]), ('https://api.deepl.com/v2/translate', 'EN-US', False))


class TestAliyun(unittest.TestCase):
    def test_the_signature_sorts_and_encodes_the_way_aliyun_wants_and_never_carries_the_secret(self):
        params = {'b': 'a b', 'a': '你好*~', 'Z': '1'}
        sig = translate.aliyun_sign(params, 'secret')
        self.assertEqual(translate._pct('a b*~/你'), 'a%20b%2A~%2F%E4%BD%A0')
        self.assertEqual(sig, translate.aliyun_sign(dict(reversed(list(params.items()))), 'secret'))      # (the order of the parameters does not matter)
        self.assertNotEqual(sig, translate.aliyun_sign({**params, 'Z': '2'}, 'secret'))
        self.assertNotEqual(sig, translate.aliyun_sign(params, 'other'))
        self.assertNotIn('secret', sig)
        import base64
        self.assertEqual(len(base64.b64decode(sig)), 20)                                                  # (a SHA-1)
        # the string that is signed, written out once: POST & %2F & the sorted query, percent-encoded twice
        import hashlib
        import hmac
        expect = base64.b64encode(hmac.new(b'secret&', ('POST&%2F&' + 'Z%3D1%26a%3D%25E4%25BD%25A0%25E5%25A5%25BD%252A~%26b%3Da%2520b').encode(), hashlib.sha1).digest()).decode()
        self.assertEqual(sig, expect)

    def test_the_request_is_a_signed_form_post_and_the_answer_is_read(self):
        sent = []

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return json.dumps({'RequestId': 'x', 'Data': {'WordCount': '2', 'Translated': '你好'}, 'Code': '200'}).encode()
        orig = translate.urllib.request.urlopen
        translate.urllib.request.urlopen = lambda req, timeout=0: (sent.append(req), R())[1]
        try:
            out = translate._aliyun_one('Hello', 'zh', 'en', BOTH, now=T0)
        finally:
            translate.urllib.request.urlopen = orig
        req = sent[0]
        body = req.data.decode()
        self.assertEqual((out, req.full_url, req.get_method()), ('你好', 'https://mt.cn-hangzhou.aliyuncs.com/', 'POST'))
        for part in ('Action=TranslateGeneral', 'SourceLanguage=en', 'TargetLanguage=zh', 'SourceText=Hello', 'AccessKeyId=id1', 'SignatureMethod=HMAC-SHA1', 'Signature='):
            self.assertIn(part, body)
        self.assertNotIn('sec1', body)                                                                    # (the secret signs, it is not sent)

    def test_texts_go_together_by_line_breaks_when_they_fit_and_alone_when_the_lines_do_not_match(self):
        sent = []

        def one(text, to, src, env):
            sent.append(text)
            return '\n'.join('译' + p for p in text.split('\n'))
        self.assertEqual(translate.aliyun_texts(['a', '', 'b'], 'zh', 'en', BOTH, one), ['译a', '', '译b'])
        self.assertEqual(sent, ['a\nb'])                                                                   # (an empty text is not sent)
        sent.clear()
        merging = lambda text, to, src, env: (sent.append(text), text.replace('\n', ' '))[1]                 # an answer without the line breaks
        out = translate.aliyun_texts(['a', 'b', 'c'], 'zh', 'en', BOTH, merging)
        self.assertEqual((out, sorted(sent[1:])), (['a', 'b', 'c'], ['a', 'b', 'c']))                      # (the joined one first, then one by one)
        big = ['x' * 3000, 'y' * 3000]
        sent.clear()
        translate.aliyun_texts(big, 'zh', 'en', BOTH, lambda t, to, src, env: (sent.append(len(t)), t)[1])
        self.assertEqual(sorted(sent), [3000, 3000])                                                       # (more than one request holds: two requests)


class Base(unittest.TestCase):
    def setUp(self):
        self.now = T0
        self.store = sa.MemoryStore(clock=lambda: self.now)
        self.calls = []

    def ask(self, texts, to='zh', src='auto', ip='1.2.3.4', env=BOTH, now=None, providers=None):
        self.now = T0 if now is None else now
        translator = lambda t, to_, src_, env_: translate.translate_texts(t, to_, src_, env_, providers or providers_that(self.calls))
        return ta.translate_request(self.store, {'to': to, 'from': src, 'texts': texts}, ip, env, now=self.now, translator=translator)


class TestRequest(Base):
    def test_a_translation_with_its_provider_and_what_is_left_of_the_day(self):
        r = self.ask(['Hello', 'World'])
        self.assertEqual((r['ok'], r['provider'], r['texts'], r['cached']), (True, 'deepl', ['deepl:Hello', 'deepl:World'], 0))
        self.assertEqual(r['left']['day'], ta.DAY_CHARS - 10)

    def test_what_was_translated_once_is_kept_costs_nothing_and_is_not_counted(self):
        self.ask(['Hello', 'World'])
        r = self.ask(['Hello', 'New', 'World'])
        self.assertEqual((r['texts'], r['cached'], [c[1] for c in self.calls]), (['deepl:Hello', 'deepl:New', 'deepl:World'], 2, [['Hello', 'World'], ['New']]))
        self.assertEqual(r['left']['day'], ta.DAY_CHARS - 10 - 3)
        r = self.ask(['Hello', 'World'], ip='9.9.9.9')                                                     # (another reader gets it from the cache too)
        self.assertEqual((r['cached'], r['provider'], len(self.calls), r['left']['day']), (2, 'cache', 2, ta.DAY_CHARS))
        r = self.ask(['Hello'], to='en')                                                                   # (the other direction is another text)
        self.assertEqual(r['cached'], 0)

    def test_a_reader_has_so_many_characters_a_day_and_the_next_day_starts_again(self):
        env = {**BOTH, 'TRANSLATE_DAY_CHARS': '1000'}
        self.ask(['x' * 600], env=env)
        with self.assertRaises(Reply) as cm:
            self.ask(['y' * 500], env=env)
        self.assertEqual((cm.exception.status, cm.exception.code), (429, 'limit'))
        self.assertEqual(len(self.calls), 1)                                                               # (the provider was not asked)
        self.assertEqual(self.ask(['z' * 300], env=env)['left']['day'], 100)                              # (what was refused was given back: 600 + 300)
        self.assertEqual(self.ask(['y' * 500], env=env, now=T0 + 86400)['ok'], True)
        self.assertEqual(self.ask(['q' * 500], env=env, ip='5.5.5.5')['ok'], True)                        # (another reader)

    def test_the_whole_site_has_a_month(self):
        env = {**BOTH, 'TRANSLATE_MONTH_CHARS': '2000', 'TRANSLATE_DAY_CHARS': '100000'}
        self.ask(['a' * 1500], env=env)
        with self.assertRaises(Reply) as cm:
            self.ask(['b' * 600], env=env, ip='8.8.8.8')
        self.assertEqual((cm.exception.status, cm.exception.code), (429, 'quota'))
        self.assertEqual(self.ask(['c' * 400], env=env, ip='8.8.8.8')['ok'], True)                        # (the 600 were given back)

    def test_a_failure_costs_the_reader_nothing(self):
        down = providers_that(self.calls, deepl=OSError('x'), aliyun=OSError('y'))
        with self.assertRaises(Reply) as cm:
            self.ask(['Hello'], providers=down)
        self.assertEqual((cm.exception.status, cm.exception.code), (502, 'fail'))
        self.assertEqual(self.ask(['Hello'])['left']['day'], ta.DAY_CHARS - 5)
        none_left = providers_that(self.calls, deepl=translate.HTTPFail(456), aliyun=translate.HTTPFail(456))
        with self.assertRaises(Reply) as cm:
            self.ask(['Another'], providers=none_left)
        self.assertEqual((cm.exception.status, cm.exception.code), (429, 'quota'))

    def test_the_second_provider_takes_over(self):
        r = self.ask(['Hello'], providers=providers_that(self.calls, deepl=translate.HTTPFail(456)))
        self.assertEqual((r['provider'], r['texts']), ('aliyun', ['aliyun:Hello']))

    def test_without_keys_or_without_the_storage_nothing_is_translated(self):
        for env in ({'IP_SALT': 'x'}, {'ALIYUN_ACCESS_KEY_ID': 'only-id'}):
            with self.assertRaises(Reply) as cm:
                self.ask(['Hello'], env=env)
            self.assertEqual((cm.exception.status, cm.exception.code), (503, 'nokey'))
        with self.assertRaises(Reply) as cm:
            ta.translate_request(None, {'to': 'zh', 'texts': ['Hello']}, '1.1.1.1', BOTH, now=T0)
        self.assertEqual((cm.exception.status, cm.exception.code), (503, 'nokey'))                        # (it could not count: it would not be safe)

    def test_only_what_is_asked_for_is_accepted(self):
        bad = [None, 'x', [], {}, {'to': 'fr', 'texts': ['a']}, {'to': 'zh', 'texts': []}, {'to': 'zh', 'texts': 'a'}, {'to': 'zh', 'texts': [1]}, {'to': 'zh', 'texts': ['a'], 'from': 'xx'},
               {'to': 'zh', 'texts': ['a'], 'from': 'zh'}, {'to': 'zh', 'texts': ['a'] * (ta.MAX_TEXTS + 1)}]
        for payload in bad:
            with self.assertRaises(Reply) as cm:
                ta.translate_request(self.store, payload, '1.1.1.1', BOTH, now=T0)
            self.assertEqual(cm.exception.status, 400, payload)
        for texts in (['x' * (ta.MAX_TEXT + 1)], ['x' * 1500] * 6):
            with self.assertRaises(Reply) as cm:
                ta.translate_request(self.store, {'to': 'zh', 'texts': texts}, '1.1.1.1', BOTH, now=T0)
            self.assertEqual((cm.exception.status, cm.exception.code), (413, 'toolong'))
        self.assertEqual(self.calls, [])

    def test_white_space_is_folded_and_control_characters_go(self):
        r = self.ask(['  Hello \n\n  world\x00 ', '   '])
        self.assertEqual((r['texts'], self.calls[0][1]), (['deepl:Hello world', ''], ['Hello world']))     # (a blank text is not sent, and comes back blank)


class TestHttp(Base):
    def go(self, method, body=None, env=BOTH, store='mine', **kw):
        h = FakeHandler(method, body, **kw)
        ta.respond(h, env, method, self.store if store == 'mine' else store, translator=lambda t, to, src, e: translate.translate_texts(t, to, src, e, providers_that(self.calls)))
        o = h.out
        return o['status'], (json.loads(o['body']) if o['body'] else None), o['headers']

    def test_post_translates_and_the_answer_is_never_cached(self):
        s, b, h = self.go('POST', {'to': 'zh', 'texts': ['Hello']}, origin='https://books.zhitree.cn')
        self.assertEqual((s, b['ok'], b['texts'], b['provider']), (200, True, ['deepl:Hello'], 'deepl'))
        self.assertEqual((h['Cache-Control'], h['Access-Control-Allow-Origin'], h['Vary']), ('no-store', 'https://books.zhitree.cn', 'Origin'))

    def test_get_says_whether_it_is_set_up_and_with_what(self):
        s, b, h = self.go('GET', origin='https://books.zhitree.cn')
        self.assertEqual((s, b['configured'], b['providers'], b['day_chars']), (200, True, ['deepl', 'aliyun'], ta.DAY_CHARS))
        self.assertNotIn('abc', json.dumps(b))                                                            # (no key, not even a piece of one)
        s, b, h = self.go('GET', env={'IP_SALT': 'x'})
        self.assertEqual((b['configured'], b['providers']), (False, []))
        s, b, h = self.go('GET', store=None)
        self.assertEqual(b['configured'], False)

    def test_a_page_of_another_site_is_refused_and_nothing_is_spent(self):
        for m, body in (('POST', {'to': 'zh', 'texts': ['Hello']}), ('GET', None)):
            s, b, h = self.go(m, body, origin='https://evil.example')
            self.assertEqual((s, b['error']), (403, 'origin'))
            self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(self.calls, [])
        self.assertIn('Access-Control-Allow-Methods', self.go('OPTIONS', origin='https://books.zhitree.cn')[2])

    def test_errors_are_told_to_the_page_in_both_languages(self):
        s, b, h = self.go('POST', {'to': 'zh', 'texts': ['x' * 2000]})
        self.assertEqual((s, b['error']), (413, 'toolong'))
        self.assertTrue(b['message'] and b['message_en'])
        s, b, h = self.go('POST', b'not json')
        self.assertEqual((s, b['error']), (400, 'request'))
        s, b, h = self.go('POST', {'to': 'zh', 'texts': ['x']}, env={'IP_SALT': 'x'})
        self.assertEqual((s, b['error']), (503, 'nokey'))

    def test_a_huge_body_is_refused_without_being_read_in_full(self):
        s, b, h = self.go('POST', b'x' * (ta.BODY_LIMIT + 10))
        self.assertEqual((s, b['error']), (413, 'toolong'))


if __name__ == '__main__':
    unittest.main()
