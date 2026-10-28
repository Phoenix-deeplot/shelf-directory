"""The Chinese of an English title (translate.py): every provider's answer is fake here (the shapes are those of the real ones); no internet.

    python -X utf8 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import aggregate as agg  # noqa: E402
import hotapi  # noqa: E402
import translate  # noqa: E402
from test_aggregate import Base, feed_site  # noqa: E402

DEEPL = {'DEEPL_API_KEY': 'abc:fx'}


def fake_post(answer, calls):
    def post(url, payload, headers):
        calls.append((url, payload, headers))
        if isinstance(answer, Exception):
            raise answer
        return answer(url, payload) if callable(answer) else answer
    return post


def deepl_says(url, payload):
    return {'translations': [{'detected_source_language': 'EN', 'text': '译：' + t} for t in payload['text']]}


class TestProviders(unittest.TestCase):
    def test_the_first_key_that_is_set_decides_and_none_is_none(self):
        self.assertEqual(translate.provider({}), '')
        self.assertEqual(translate.provider({'DEEPL_API_KEY': ' ', 'GOOGLE_TRANSLATE_KEY': ''}), '')
        self.assertEqual(translate.provider({'GOOGLE_TRANSLATE_KEY': 'g'}), 'google')
        self.assertEqual(translate.provider({'GOOGLE_TRANSLATE_KEY': 'g', 'DEEPL_API_KEY': 'd'}), 'deepl')
        self.assertEqual(translate.provider({'LIBRETRANSLATE_URL': 'https://lt.example'}), 'libre')
        self.assertEqual(translate.provider({'LIBRETRANSLATE_URL': 'http://lt.example'}), '')              # (plain http is not used)

    def test_deepl_a_free_key_goes_to_the_free_host_and_the_key_is_a_header_not_an_address(self):
        calls = []
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.translate(['Hello world', 'Second'], DEEPL)
        self.assertEqual(out, ['译：Hello world', '译：Second'])
        url, payload, headers = calls[0]
        self.assertEqual((url, payload['target_lang'], payload['text']), ('https://api-free.deepl.com/v2/translate', 'ZH', ['Hello world', 'Second']))
        self.assertEqual(headers['Authorization'], 'DeepL-Auth-Key abc:fx')
        self.assertNotIn('abc', url)
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            translate.translate(['x one'], {'DEEPL_API_KEY': 'paidkey'})
        self.assertEqual(calls[1][0], 'https://api.deepl.com/v2/translate')

    def test_google_answers_are_html_entities_and_libretranslate_may_want_a_key(self):
        calls = []
        g = {'data': {'translations': [{'translatedText': '谁在说 &quot;你好&quot; &amp; 再见'}]}}
        with mock.patch.object(translate, '_post', fake_post(g, calls)):
            self.assertEqual(translate.translate(['who says hello'], {'GOOGLE_TRANSLATE_KEY': 'k'}), ['谁在说 "你好" & 再见'])
        self.assertEqual((calls[0][0], calls[0][1]['target'], calls[0][2]['X-Goog-Api-Key']), ('https://translation.googleapis.com/language/translate/v2', 'zh-CN', 'k'))
        with mock.patch.object(translate, '_post', fake_post({'translatedText': ['你好吗']}, calls)):
            self.assertEqual(translate.translate(['how are you'], {'LIBRETRANSLATE_URL': 'https://lt.example/', 'LIBRETRANSLATE_KEY': 'lk'}), ['你好吗'])
        self.assertEqual((calls[1][0], calls[1][1]['api_key'], calls[1][1]['target']), ('https://lt.example/translate', 'lk', 'zh'))

    def test_what_is_not_chinese_is_dropped_and_a_bad_shape_is_no_answer(self):
        with mock.patch.object(translate, '_post', fake_post({'translations': [{'text': 'same words'}, {'text': ''}, {'text': '好的\x00\n标题'}]}, [])):
            self.assertEqual(translate.translate(['same words', 'two words', 'three words'], DEEPL), ['', '', '好的 标题'])
        for bad in ({'translations': [{'text': '你好'}]}, {'nothing': 1}, {'translations': None}, OSError('HTTP 456'), ValueError('x')):
            with mock.patch.object(translate, '_post', fake_post(bad, [])):
                self.assertEqual(translate.translate(['a title', 'b title'], DEEPL), ['', ''], bad)               # (a count that does not match is not trusted either)

    def test_no_key_asks_nobody(self):
        calls = []
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            self.assertEqual(translate.translate(['a title'], {}), [''])
        self.assertEqual(calls, [])

    def test_titles_go_in_batches_and_a_batch_that_fails_ends_the_run(self):
        calls = []
        many = ['Title number %d' % i for i in range(translate.BATCH + 5)]
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.translate(many, DEEPL)
        self.assertEqual([len(c[1]['text']) for c in calls], [translate.BATCH, 5])
        self.assertTrue(all(out))
        answers = [deepl_says('', {'text': many[:translate.BATCH]}), OSError('down')]
        with mock.patch.object(translate, '_post', side_effect=answers):
            out = translate.translate(many, DEEPL)
        self.assertTrue(all(out[:translate.BATCH]) and not any(out[translate.BATCH:]))

    def test_what_is_worth_translating(self):
        self.assertTrue(translate.wants('Hello world') and translate.wants('GPT-5 is here') and translate.wants('Show HN: 我 built it'))     # (one Chinese character in an English title does not make it Chinese)
        self.assertFalse(translate.wants('这是一个中文标题 with API') or translate.wants('2026') or translate.wants('') or translate.wants('这是中文'))


class TestTheCacheAndTheRun(Base):
    def setUp(self):
        super().setUp()
        translate.MEMORY.clear()
        self.addCleanup(translate.MEMORY.clear)

    def test_each_title_is_asked_once_and_kept_in_the_registry(self):
        calls = []
        rows = [{'title': 'First story'}, {'title': 'Second story'}, {'title': '一个中文标题'}, {'title': 'First story'}]
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.titles(rows, DEEPL)
            again = translate.titles(rows + [{'title': 'Third story'}], DEEPL)
        self.assertEqual(out, {'First story': '译：First story', 'Second story': '译：Second story'})
        self.assertEqual([c[1]['text'] for c in calls], [['First story', 'Second story'], ['Third story']])
        self.assertEqual(again['First story'], '译：First story')
        kept = json.load(open(os.path.join(self.tmp, 'registry', 'titles_zh.json'), encoding='utf-8'))
        self.assertEqual(sorted(kept.values()), ['译：First story', '译：Second story', '译：Third story'])

    def test_without_a_key_what_is_known_is_used_and_nothing_is_asked(self):
        translate.save_cache({translate.key('Known one'): '已知的一个'})
        calls = []
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.titles([{'title': 'Known one'}, {'title': 'Unknown one'}], {})
        self.assertEqual((out, calls), ({'Known one': '已知的一个'}, []))

    def test_only_so_many_new_titles_are_sent_per_run_the_newest_first(self):
        calls = []
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.titles([{'title': 'Title %s' % c} for c in 'ABCDE'], DEEPL, limit=3)
        self.assertEqual((sorted(out), calls[0][1]['text']), (['Title A', 'Title B', 'Title C'], ['Title A', 'Title B', 'Title C']))

    def test_a_service_without_a_file_keeps_them_in_memory_and_reads_the_file_too(self):
        translate.save_cache({translate.key('On disk'): '在磁盘上'})
        calls = []
        with mock.patch.object(translate, '_post', fake_post(deepl_says, calls)):
            out = translate.titles([{'title': 'On disk'}, {'title': 'New title'}], DEEPL, persist=False)
            translate.titles([{'title': 'New title'}], DEEPL, persist=False)
        self.assertEqual(out, {'On disk': '在磁盘上', 'New title': '译：New title'})
        self.assertEqual(len(calls), 1)
        kept = json.load(open(os.path.join(self.tmp, 'registry', 'titles_zh.json'), encoding='utf-8'))
        self.assertEqual(list(kept.values()), ['在磁盘上'])                                           # (the file was not written)

    def test_the_cache_is_bounded_and_a_broken_file_is_an_empty_one(self):
        translate.save_cache({'k%d' % i: 'z' for i in range(translate.CACHE_MAX + 10)})
        d = translate.load_cache()
        self.assertEqual((len(d), 'k0' in d, 'k%d' % (translate.CACHE_MAX + 9) in d), (translate.CACHE_MAX, False, True))
        open(translate.cache_path(), 'w').write('not json')
        self.assertEqual(translate.load_cache(), {})


class TestInTheDirectoryAndTheHotLists(Base):
    def setUp(self):
        super().setUp()
        translate.MEMORY.clear()
        self.addCleanup(translate.MEMORY.clear)

    def test_the_directory_gets_title_zh_for_the_english_titles_only(self):
        feed_site(self.site, posts=(('Hello world', '2026-10-01'), ('Second post', '2026-10-02')))
        agg.approve(self.site.url + '/feed.xml')
        with mock.patch.dict(os.environ, {'DEEPL_API_KEY': 'k:fx'}), mock.patch.object(translate, '_post', fake_post(deepl_says, [])):
            d = json.loads(agg.build()['directory.json'])
        self.assertEqual({i['title']: i.get('title_zh') for i in d['items']}, {'Hello world': '译：Hello world', 'Second post': '译：Second post'})

    def test_without_a_key_the_directory_is_what_it_was(self):
        feed_site(self.site)
        agg.approve(self.site.url + '/feed.xml')
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ('DEEPL_API_KEY', 'GOOGLE_TRANSLATE_KEY', 'LIBRETRANSLATE_URL'):
                os.environ.pop(k, None)
            d = json.loads(agg.build()['directory.json'])
        self.assertNotIn('title_zh', d['items'][0])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'registry', 'titles_zh.json')))

    def test_a_hot_list_is_translated_when_the_page_asks_and_only_an_english_one(self):
        rows = [hotapi._norm({'title': 'An English headline', 'url': 'https://a.example/1'}), hotapi._norm({'title': 'A second one', 'url': 'https://a.example/2'})]
        with mock.patch.object(hotapi, 'entries', return_value=(rows, 1000)), mock.patch.object(translate, '_post', fake_post(deepl_says, [])):
            st, _, body, cache = hotapi.handle({'p': ['hackernews'], 'format': ['json'], 'tr': ['zh']}, DEEPL)
            plain = json.loads(hotapi.handle({'p': ['hackernews'], 'format': ['json']}, DEEPL)[2])['items']
            zh_list = json.loads(hotapi.handle({'p': ['weibo'], 'format': ['json'], 'tr': ['zh']}, DEEPL)[2])['items']
        items = json.loads(body)['items']
        self.assertEqual((st, [i['title_zh'] for i in items]), (200, ['译：An English headline', '译：A second one']))
        self.assertTrue(all('title_zh' not in i for i in plain + zh_list))               # not asked for, or a Chinese list: nothing is sent
        self.assertEqual(rows[0].get('title_zh'), None)                                   # (what is kept in memory for the list is not changed)

    def test_the_hot_service_never_fails_because_of_the_translation(self):
        rows = [hotapi._norm({'title': 'An English headline', 'url': 'https://a.example/1'})]
        with mock.patch.object(hotapi, 'entries', return_value=(rows, 1000)), mock.patch.object(translate, '_post', fake_post(OSError('HTTP 500'), [])):
            st, _, body, _ = hotapi.handle({'p': ['hackernews'], 'format': ['json'], 'tr': ['zh']}, DEEPL)
        self.assertEqual((st, json.loads(body)['items'][0]['title'], 'title_zh' in json.loads(body)['items'][0]), (200, 'An English headline', False))


if __name__ == '__main__':
    unittest.main()
