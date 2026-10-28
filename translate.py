"""translate.py: the Chinese for an English title, for the small grey line under it (optional: off until a key is set).

Providers, the first one whose key is set (environment variables; in the GitHub Action they are repository secrets, see .github/workflows/daily.yml):
    DEEPL_API_KEY          DeepL (a key that ends with ":fx" is a Free one: api-free.deepl.com; any other: api.deepl.com)
    GOOGLE_TRANSLATE_KEY   Google Cloud Translation (basic, v2)
    LIBRETRANSLATE_URL     a LibreTranslate server (https://host), and LIBRETRANSLATE_KEY when it wants one
Only titles are sent (never what a reader read or did, never anything about a reader). The daily build sends the titles of the English articles that have no Chinese yet and keeps the answers in
registry/titles_zh.json, so that each title is paid for once; the hot-list service sends the titles of a list the page asked to have translated (`&tr=zh`), and remembers them while the instance lives.
A provider that fails is the same as no provider: the page then shows the title as it is. Standard library only.
"""
import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request

TIMEOUT = 15
BATCH = 40                         # titles in one request
TITLE_MAX = 200
CACHE_MAX = 4000                   # titles kept in registry/titles_zh.json (the oldest go first)
NEW_PER_RUN = 300                  # new titles the daily build sends at most (the newest articles first)
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
MEMORY = {}                        # what this instance has translated (the hot-list service has no file to write)


def provider(env):
    """-> 'deepl' | 'google' | 'libre' | '' (no key set)."""
    env = env or {}
    if str(env.get('DEEPL_API_KEY') or '').strip():
        return 'deepl'
    if str(env.get('GOOGLE_TRANSLATE_KEY') or '').strip():
        return 'google'
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


def _post(url, payload, headers):
    """-> the answer of a POST as parsed JSON. Raises OSError / ValueError (the callers treat both as "no answer")."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'User-Agent': 'ZhiTreeBooks-Titles/1', **headers}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(2_000_000).decode('utf-8'))
    except urllib.error.HTTPError as e:
        e.close()
        raise OSError('HTTP %d' % e.code)


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


PROVIDERS = {'deepl': _deepl, 'google': _google, 'libre': _libre}


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
