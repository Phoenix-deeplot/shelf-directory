"""translate.py: the Chinese for an English title, for the small grey line under it (optional: off until a key is set).

Providers, the first one whose key is set (environment variables; in the GitHub Action they are repository secrets, see .github/workflows/daily.yml):
    DEEPL_API_KEY          DeepL (a key that ends with ":fx" is a Free one: api-free.deepl.com; any other: api.deepl.com)
    GOOGLE_TRANSLATE_KEY   Google Cloud Translation (basic, v2)
    LIBRETRANSLATE_URL     a LibreTranslate server (https://host), and LIBRETRANSLATE_KEY when it wants one
Only titles are sent (never what a reader read or did, never anything about a reader). The daily build sends the titles of the English articles that have no Chinese yet and keeps the answers in
registry/titles_zh.json, so that each title is paid for once; the hot-list service sends the titles of a list the page asked to have translated (`&tr=zh`), and remembers them while the instance lives.
A provider that fails is the same as no provider: the page then shows the title as it is. Standard library only.

Also (for the reading page's "translate" button, see translateapi.py): the TEXT of an article, in either direction (zh <-> en and a few more), by DeepL or by Aliyun's machine translation
(general edition: ALIYUN_ACCESS_KEY_ID + ALIYUN_ACCESS_KEY_SECRET; the secret only signs a request, it is never sent). translate_texts() tries them in the order TRANSLATE_PROVIDER says
(default: DeepL first) and takes the next one when a provider fails or has no allowance left.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 15
BATCH = 40                         # titles in one request
TITLE_MAX = 200
CACHE_MAX = 4000                   # titles kept in registry/titles_zh.json (the oldest go first)
NEW_PER_RUN = 300                  # new titles the daily build sends at most (the newest articles first)
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
MEMORY = {}                        # what this instance has translated (the hot-list service has no file to write)


def provider(env):
    """-> 'deepl' | 'google' | 'aliyun' | 'libre' | '' (no key set)."""
    env = env or {}
    if str(env.get('DEEPL_API_KEY') or '').strip():
        return 'deepl'
    if str(env.get('GOOGLE_TRANSLATE_KEY') or '').strip():
        return 'google'
    if aliyun_keys(env):
        return 'aliyun'
    if str(env.get('LIBRETRANSLATE_URL') or '').strip().startswith('https://'):
        return 'libre'
    return ''


def wants(title):
    """True for a title that is English (or another Latin-script language): it has words, and next to no Chinese."""
    t = str(title or '')
    return len(re.findall(r'[A-Za-z]{3}', t)) >= 1 and len(re.findall(r'[一-鿿]', t)) < 2


def key(title):
    return hashlib.sha1(re.sub(r'\s+', ' ', str(title or '')).strip().lower().encode('utf-8')).hexdigest()[:16]


def cache_path():
    import aggregate as agg
    return os.path.join(os.path.dirname(agg.reg_path()), 'titles_zh.json')


def load_cache(path=None):
    try:
        with open(path or cache_path(), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str) and v} if isinstance(d, dict) else {}


def save_cache(d, path=None):
    if len(d) > CACHE_MAX:
        d = dict(list(d.items())[-CACHE_MAX:])
    with open(path or cache_path(), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, ensure_ascii=False, indent=0, sort_keys=False)
        f.write('\n')


class HTTPFail(OSError):
    """An answer that is an error (DeepL says 456 when the allowance is used up, 429 for too many requests)."""

    def __init__(self, code):
        super().__init__('HTTP %d' % code)
        self.code = code


def _post(url, payload, headers):
    """-> the answer of a POST as parsed JSON. Raises OSError / ValueError (the callers treat both as "no answer")."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'User-Agent': 'ZhiTreeBooks-Titles/1', **headers}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(2_000_000).decode('utf-8'))
    except urllib.error.HTTPError as e:
        e.close()
        raise HTTPFail(e.code)


def _deepl(texts, env):
    k = str(env['DEEPL_API_KEY']).strip()
    host = 'api-free.deepl.com' if k.endswith(':fx') else 'api.deepl.com'
    d = _post('https://%s/v2/translate' % host, {'text': texts, 'target_lang': 'ZH'}, {'Authorization': 'DeepL-Auth-Key ' + k})
    return [str(x.get('text') or '') for x in d['translations']]


def _google(texts, env):
    k = str(env['GOOGLE_TRANSLATE_KEY']).strip()
    d = _post('https://translation.googleapis.com/language/translate/v2', {'q': texts, 'target': 'zh-CN', 'format': 'text'}, {'X-Goog-Api-Key': k})
    return [html.unescape(str(x.get('translatedText') or '')) for x in d['data']['translations']]


def _libre(texts, env):
    base = str(env['LIBRETRANSLATE_URL']).strip().rstrip('/')
    body = {'q': texts, 'source': 'auto', 'target': 'zh', 'format': 'text'}
    if str(env.get('LIBRETRANSLATE_KEY') or '').strip():
        body['api_key'] = str(env['LIBRETRANSLATE_KEY']).strip()
    d = _post(base + '/translate', body, {})
    out = d['translatedText']
    return [str(x) for x in (out if isinstance(out, list) else [out])]


def _aliyun(texts, env):
    return aliyun_texts(texts, 'zh', 'auto', env)


PROVIDERS = {'deepl': _deepl, 'google': _google, 'aliyun': _aliyun, 'libre': _libre}


def translate(titles, env):
    """-> a list the length of `titles`: the Chinese, or '' where there is none (no provider, a failure, an answer of another shape)."""
    which = provider(env)
    out = [''] * len(titles)
    if not which:
        return out
    for i in range(0, len(titles), BATCH):
        chunk = [str(t)[:TITLE_MAX] for t in titles[i:i + BATCH]]
        try:
            got = PROVIDERS[which](chunk, env)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            break                                                                   # (a provider that fails is no provider: the rest stays English)
        if len(got) != len(chunk):
            break
        for j, zh in enumerate(got):
            zh = re.sub(r'\s+', ' ', _CTRL.sub(' ', zh)).strip()[:TITLE_MAX]
            if zh and zh != chunk[j] and re.search(r'[一-鿿]', zh):
                out[i + j] = zh
    return out


def titles(rows, env, persist=True, limit=NEW_PER_RUN):
    """-> {title: chinese} for the English titles among `rows` (dicts with 'title'; the newest first): what the cache knows, and, for as many new ones as `limit`, what the provider says
    (kept in registry/titles_zh.json when `persist`, else in this instance's memory only)."""
    disk = load_cache()
    store = disk if persist else MEMORY                                              # (what is new goes to the file, or to this instance's memory; what the file knows is used either way)
    want, seen = [], set()
    for r in rows:
        t = str(r.get('title') or '')
        if t and t not in seen and wants(t):
            seen.add(t)
            want.append(t)
    out, new = {}, []
    for t in want:
        zh = store.get(key(t)) or disk.get(key(t))
        if zh:
            out[t] = zh
        else:
            new.append(t)
    new = new[:limit]
    if new and provider(env):
        for t, zh in zip(new, translate(new, env)):
            if zh:
                out[t] = store[key(t)] = zh
        if persist:
            save_cache(disk)
    return out


# ======================================================================= the text of an article (api/translate.py)
DEEPL_TARGET = {'zh': 'ZH', 'en': 'EN-US'}
DEEPL_SOURCE = {'en': 'EN', 'zh': 'ZH', 'ja': 'JA', 'ko': 'KO', 'fr': 'FR', 'de': 'DE', 'es': 'ES', 'ru': 'RU', 'it': 'IT', 'pt': 'PT'}
ALIYUN_LANG = {'zh': 'zh', 'en': 'en', 'ja': 'ja', 'ko': 'ko', 'fr': 'fr', 'de': 'de', 'es': 'es', 'ru': 'ru', 'it': 'it', 'pt': 'pt', 'auto': 'auto'}
LANGS = tuple(sorted(set(DEEPL_SOURCE) | {'auto'}))
ALIYUN_URL = 'https://mt.cn-hangzhou.aliyuncs.com/'
ALIYUN_CHARS = 4500                # the general edition takes up to 5000 characters in one request


class Unavailable(Exception):
    """No provider could translate. .code: 'nokey' (none is set up), 'quota' (every one that is set up has no allowance left), 'fail' (they did not answer, or oddly)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def aliyun_keys(env):
    env = env or {}
    return bool(str(env.get('ALIYUN_ACCESS_KEY_ID') or '').strip() and str(env.get('ALIYUN_ACCESS_KEY_SECRET') or '').strip())


def configured(env):
    """-> the names of the providers that can translate a text, in the order they are tried (TRANSLATE_PROVIDER=aliyun puts Aliyun first)."""
    have = [n for n, ok in (('deepl', bool(str((env or {}).get('DEEPL_API_KEY') or '').strip())), ('aliyun', aliyun_keys(env))) if ok]
    if str((env or {}).get('TRANSLATE_PROVIDER') or '').strip().lower() == 'aliyun':
        have.sort(key=lambda n: n != 'aliyun')
    return have


def _pct(x):
    return urllib.parse.quote(str(x), safe='~')                                       # (RFC 3986: a space is %20, * is %2A, ~ stays: what Aliyun's signature wants)


def aliyun_sign(params, secret):
    """The signature of an Aliyun RPC request (HMAC-SHA1 over the sorted, percent-encoded parameters; the key is the secret and an &)."""
    query = '&'.join('%s=%s' % (_pct(k), _pct(params[k])) for k in sorted(params))
    to_sign = 'POST&%2F&' + _pct(query)
    return base64.b64encode(hmac.new((str(secret) + '&').encode('utf-8'), to_sign.encode('utf-8'), hashlib.sha1).digest()).decode('ascii')


def _aliyun_one(text, to, src, env, now=None):
    params = {'Format': 'JSON', 'Version': '2018-10-12', 'AccessKeyId': str(env['ALIYUN_ACCESS_KEY_ID']).strip(), 'SignatureMethod': 'HMAC-SHA1',
              'Timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() if now is None else now)), 'SignatureVersion': '1.0', 'SignatureNonce': uuid.uuid4().hex,
              'Action': 'TranslateGeneral', 'FormatType': 'text', 'SourceLanguage': ALIYUN_LANG.get(src, 'auto'), 'TargetLanguage': ALIYUN_LANG[to], 'SourceText': text, 'Scene': 'general'}
    params['Signature'] = aliyun_sign(params, str(env['ALIYUN_ACCESS_KEY_SECRET']).strip())
    body = '&'.join('%s=%s' % (_pct(k), _pct(v)) for k, v in params.items()).encode('utf-8')
    req = urllib.request.Request(ALIYUN_URL, data=body, method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'ZhiTreeBooks-Translate/1'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read(2_000_000).decode('utf-8'))
    except urllib.error.HTTPError as e:
        e.close()
        raise HTTPFail(e.code)
    if str(d.get('Code')) != '200':
        raise OSError('aliyun answered %s' % d.get('Code'))
    return str(d['Data']['Translated'])


def aliyun_texts(texts, to, src, env, one=None):
    """Translate texts (each one paragraph, no line breaks inside): as many as fit ALIYUN_CHARS go in one request, joined by line breaks and told apart again by them; when the answer has another
    number of lines, those texts are sent one by one."""
    one = one or _aliyun_one
    out = [''] * len(texts)
    group, size = [], 0
    groups = []
    for i, t in enumerate(texts):
        if not t:
            continue
        if group and size + len(t) + 1 > ALIYUN_CHARS:
            groups.append(group)
            group, size = [], 0
        group.append(i)
        size += len(t) + 1
    if group:
        groups.append(group)
    singles = []
    for g in groups:
        if len(g) > 1:
            got = one('\n'.join(texts[i] for i in g), to, src, env).split('\n')
            if len(got) == len(g):
                for i, x in zip(g, got):
                    out[i] = x.strip()
                continue
        singles += g
    if singles:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, x in zip(singles, pool.map(lambda i: one(texts[i], to, src, env), singles)):
                out[i] = x.strip()
    return out


def deepl_texts(texts, to, src, env):
    k = str(env['DEEPL_API_KEY']).strip()
    host = 'api-free.deepl.com' if k.endswith(':fx') else 'api.deepl.com'
    payload = {'text': texts, 'target_lang': DEEPL_TARGET[to]}
    if src in DEEPL_SOURCE:
        payload['source_lang'] = DEEPL_SOURCE[src]
    d = _post('https://%s/v2/translate' % host, payload, {'Authorization': 'DeepL-Auth-Key ' + k})
    return [str(x.get('text') or '') for x in d['translations']]


TEXT_PROVIDERS = {'deepl': deepl_texts, 'aliyun': aliyun_texts}


def translate_texts(texts, to, src, env, providers=None):
    """-> (the provider that did it, the translations: a list as long as `texts`). Raises Unavailable.
    A provider that has no allowance left (DeepL: 456; both: 429) or does not answer is skipped for the next one."""
    names = configured(env)
    if not names:
        raise Unavailable('nokey')
    providers = providers or TEXT_PROVIDERS
    why = 'fail'
    for name in names:
        try:
            got = providers[name](list(texts), to, src, env)
        except HTTPFail as e:
            why = 'quota' if e.code in (456, 429) else 'fail'
            continue
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            why = 'fail'
            continue
        if len(got) == len(texts):
            return name, [re.sub(r'[ \t]+', ' ', _CTRL.sub(' ', x)).strip() if x else '' for x in got]
        why = 'fail'
    raise Unavailable(why)
