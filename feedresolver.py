"""Resolve subscription links; fetch through aggregate's bounded, SSRF-safe transport.

No cookies, user credentials, arbitrary API mappings or public RSSHub fallback.
Every returned candidate still has to pass the ordinary feed parser.
"""
import json
import re
from urllib.parse import urlsplit

import aggregate as agg
from submission import Reply


def fail(code, zh, en):
    raise Reply(422, code, zh, en)


def rsshub(route, env):
    base = str((env or {}).get('RSSHUB_BASE') or '').strip().rstrip('/')
    if not base:
        fail('rsshub_required', '已识别平台，但站长尚未配置 RSSHub；请提供原生 RSS，或暂时仅收录链接。',
             'Platform recognized, but RSSHub is not configured. Supply a native feed or save the link.')
    agg.check_url_syntax(base)
    p = urlsplit(base)
    if p.query or p.fragment:
        fail('rsshub_config', 'RSSHub 地址配置不正确，请联系站长。', 'The RSSHub base URL is invalid; contact the site owner.')
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', route) or any(s in ('.', '..') for s in route.split('/')):
        fail('protocol', 'RSSHub 路由格式不正确。', 'Invalid RSSHub route.')
    return base + route


def resolve(raw, env=None):
    """Return (candidate URL, resolution details); not a claim that a feed works."""
    value = raw.strip()
    m = re.match(r'^([a-z][a-z0-9+.-]*):', value, re.I)
    scheme = m[1].lower() if m else ''
    method = 'native'
    if scheme == 'rsshub':
        target = rsshub('/' + value.split(':', 1)[1].lstrip('/'), env)
        return target, {'method': 'rsshub', 'platform': 'RSSHub', 'scope': 'route'}
    if scheme in ('feed', 'webcal', 'itpc', 'podcast', 'pcast'):
        rest = value.split(':', 1)[1]
        value = rest if rest.startswith(('https://', 'http://')) else 'https://' + rest.lstrip('/')
        method = 'protocol'
    elif scheme and scheme not in ('http', 'https'):
        fail('protocol', '暂不支持这个应用协议，请从应用里复制节目、频道或订阅源的网页分享链接。',
             'Unsupported app protocol. Copy the web share link of the show, channel or feed instead.')
    url = agg.normalize_input(value)
    if not agg.ALLOW_LOCAL:
        agg.check_url_syntax(url)
    p = urlsplit(url)
    host = (p.hostname or '').lower().removeprefix('www.')
    resolution = {'method': method, 'platform': host, 'scope': 'updates'}
    # pod.link IDs are Apple collection IDs: no need to scrape the landing page.
    podcast_id = None
    if host == 'pod.link' and re.fullmatch(r'/\d{5,15}/?', p.path):
        podcast_id = p.path.strip('/')
    if host in ('podcasts.apple.com', 'itunes.apple.com'):
        match = re.search(r'/id(\d{5,15})(?:/|$)', p.path)
        if match:
            podcast_id = match[1]
    if podcast_id:
        country = re.match(r'^/([a-z]{2})/', p.path) if host != 'pod.link' else None
        lookup = 'https://itunes.apple.com/lookup?id=' + podcast_id + '&entity=podcast'
        if country:
            lookup += '&country=' + country[1]
        _, headers, body = agg.fetch(lookup, 'application/json', max_bytes=256 * 1024)
        try:
            data = json.loads(agg.decode(body, headers))
            rows = data.get('results', [])
        except (ValueError, AttributeError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if (isinstance(row, dict) and row.get('kind') == 'podcast'
                    and str(row.get('collectionId')) == podcast_id and row.get('feedUrl')):
                candidate = agg.normalize_input(str(row['feedUrl']))
                if not agg.ALLOW_LOCAL:
                    agg.check_url_syntax(candidate)
                return candidate, {'method': 'apple_lookup', 'platform': 'Apple Podcasts', 'scope': 'show'}
        fail('feed_missing', '已识别播客，但目录没有提供公开 RSS。请复制节目主页或 RSS 地址。',
             'Podcast recognized, but no public RSS was returned. Copy the show home page or its RSS URL.')
    if host == 'github.com':
        match = re.fullmatch(r'/([\w.-]+)/([\w.-]+?)(?:\.git)?/?', p.path)
        if match:
            target = 'https://github.com/' + match[1] + '/' + match[2] + '/releases.atom'
            return target, {'method': 'github_releases', 'platform': 'GitHub', 'scope': 'releases'}
    if host == 'space.bilibili.com':
        match = re.fullmatch(r'/(\d+)(?:/.*)?', p.path)
        if match:
            return rsshub('/bilibili/user/video/' + match[1], env), {'method': 'rsshub', 'platform': 'Bilibili', 'scope': 'videos'}
    if host in ('open.spotify.com', 'music.163.com', 'y.qq.com', 'music.apple.com', 'qishui.douyin.com'):
        fail('platform_adapter', '这是音乐或节目平台分享链接，当前没有对应的更新订阅适配器；可仅收录，播客请提供节目 RSS 或 Apple Podcasts 链接。',
             'This platform share link needs an update adapter. Save it as a link; for podcasts use RSS or an Apple Podcasts URL.')
    if host in ('x.com', 'twitter.com', 'xiaohongshu.com', 'www.zhihu.com', 'zhihu.com'):
        fail('platform_adapter', '已识别平台；持续订阅需要对应 RSSHub 路由及实例权限。请提供可用的 RSS 地址，或暂时仅收录。',
             'Following this platform requires a working RSSHub route and instance access. Supply a working feed or save the link.')
    return url, resolution


def page_candidate(pre):
    """Only explicit feed declarations or a YouTube channel ID; never guess an API schema."""
    if not pre:
        return None
    final, headers, body = pre
    text = agg.decode(body, headers)
    feeds = agg.parse_page(text, final).get('feeds', [])
    if feeds:
        return feeds[0]
    host = (urlsplit(final).hostname or '').lower()
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        match = re.search(r'"(?:channelId|externalId)"\s*:\s*"(UC[\w-]{22})"', text)
        if match:
            return 'https://www.youtube.com/feeds/videos.xml?channel_id=' + match[1]
    return None
