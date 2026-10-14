"""mdsafe.py: Markdown -> HTML for TEXT WRITTEN BY STRANGERS (hosted posts). Standard library only.

Based on the converter of my-archive's seo.py, with a stricter policy because nobody vouches for this text:
* everything that is not a tag built here is HTML-escaped, so a post cannot inject markup or script;
* links are http(s) only and carry rel="nofollow ugc noopener noreferrer" target="_blank"; relative links, javascript:, data:, mailto: are dropped (the text stays);
* pictures are NOT embedded (an image address is a tracking pixel in somebody else's hands): they become a link "[图片] alt";
* lines are cut at LINE_MAX characters and nesting at DEPTH_MAX levels, so a hostile text cannot make the renderer slow or recurse forever.
"""
import re
import urllib.parse
from html import escape as esc

LINE_MAX = 4000
DEPTH_MAX = 12
REL = 'nofollow ugc noopener noreferrer'
_PLACE = re.compile('\x00(\\d+)\x00')


def _safe_url(url):
    url = url.strip().replace('\x00', '')
    if not url or len(url) > 2000:
        return None
    if url.startswith('#'):
        return url
    parts = urllib.parse.urlsplit(url)
    return url if parts.scheme.lower() in ('http', 'https') and parts.netloc else None


def _a(href, inner):
    return '<a href="%s" rel="%s" target="_blank">%s</a>' % (esc(href), REL, inner) if not href.startswith('#') else '<a href="%s">%s</a>' % (esc(href), inner)


def _inline(text, stash=None):
    top = stash is None
    stash = [] if top else stash

    def keep(h):
        stash.append(h)
        return '\x00%d\x00' % (len(stash) - 1)

    text = text.replace('\x00', '')
    text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|~<>])', lambda m: keep(esc(m.group(1), quote=False)), text)
    text = re.sub(r'(`+)(.+?)\1', lambda m: keep('<code>%s</code>' % esc(m.group(2).strip(), quote=False)), text)

    def image(m):                                        # never embedded: a link to the picture, or just its description
        src = _safe_url(m.group(2))
        label = '[图片] ' + m.group(1) if m.group(1) else '[图片]'
        return keep(_a(src, esc(label, quote=False))) if src and not src.startswith('#') else esc(label, quote=False)

    text = re.sub(r'!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)', image, text)

    def link(m):
        href = _safe_url(m.group(2))
        inner = _inline(m.group(1), stash)
        return keep(_a(href, inner)) if href else inner

    text = re.sub(r'\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)', link, text)
    text = re.sub(r'<(https?://[^\s<>]+)>', lambda m: keep(_a(m.group(1), esc(m.group(1), quote=False))), text)
    text = esc(text, quote=False)
    text = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(?=\S)(.+?)(?<=\S)\*', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)', r'<em>\1</em>', text)
    text = re.sub(r'~~(?=\S)(.+?)(?<=\S)~~', r'<del>\1</del>', text)
    text = re.sub(r' {2,}\n', '<br>\n', text)
    if top:
        while _PLACE.search(text):
            text = _PLACE.sub(lambda m: stash[int(m.group(1))], text)
    return text


_FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)')
_HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$')
_HR = re.compile(r'^\s{0,3}([-*_])(?:\s*\1){2,}\s*$')
_LIST = re.compile(r'^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$')
_QUOTE = re.compile(r'^\s{0,3}>')
_SETEXT = re.compile(r'^\s{0,3}(=+|-+)\s*$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$')


def _cells(line):
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith('\\|'):
        line = line[:-1]
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', line)]


def _is_table(line, nxt):
    return '|' in line and '|' in nxt and _TABLE_SEP.match(nxt) is not None and len(_cells(line)) == len(_cells(nxt))


def _starts_block(line, nxt=''):
    return bool(_HEADING.match(line) or _HR.match(line) or _FENCE.match(line) or _QUOTE.match(line) or _LIST.match(line) or _is_table(line, nxt))


def _list(lines, i, depth):
    n = len(lines)
    m = _LIST.match(lines[i].expandtabs(4))
    base = len(m.group(1))
    ordered = m.group(2)[0].isdigit()
    start = int(m.group(2)[:-1]) if ordered else 1
    items, cur = [], None
    while i < n:
        line = lines[i].expandtabs(4)
        if not line.strip():                      # a blank line belongs to the list only if more of it follows
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                nxt = lines[j].expandtabs(4)
                ind = len(nxt) - len(nxt.lstrip())
                mm = _LIST.match(nxt)
                if ind > base or (mm and ind == base and mm.group(2)[0].isdigit() == ordered):
                    if cur is not None:
                        cur.append('')
                    i += 1
                    continue
            break
        mm = _LIST.match(line)
        ind = len(line) - len(line.lstrip())
        if mm and ind == base and mm.group(2)[0].isdigit() == ordered:
            cur = [mm.group(3)]
            items.append(cur)
        elif ind > base and cur is not None:
            cur.append(line[min(ind, base + 2):])
        elif not mm and cur is not None and not _starts_block(line):
            cur.append(line.strip())
        else:
            break
        i += 1
    lis = []
    for body in items:
        box = ''
        mt = re.match(r'^\[( |x|X)\]\s+', body[0])
        if mt:
            box = '☑ ' if mt.group(1) in 'xX' else '☐ '
            body[0] = body[0][mt.end():]
        inner = _blocks(body, depth + 1)
        if '' not in body:
            inner = re.sub(r'^<p>((?:(?!</p>).)*)</p>', r'\1', inner, count=1, flags=re.S)
        lis.append('<li>%s%s</li>' % (box, inner))
    tag = 'ol' if ordered else 'ul'
    open_tag = '<ol start="%d">' % start if ordered and start != 1 else '<%s>' % tag
    return '%s\n%s\n</%s>' % (open_tag, '\n'.join(lis), tag), i


def _blocks(lines, depth=0):
    if depth > DEPTH_MAX:
        raise RecursionError('too deeply nested')
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            fence, lang, body = m.group(1), m.group(2), []
            closing = re.compile(r'^\s{0,3}%s{%d,}\s*$' % (re.escape(fence[0]), len(fence)))
            i += 1
            while i < n and not closing.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            out.append('<pre><code%s>%s</code></pre>' % (' class="language-%s"' % esc(lang) if lang else '', esc('\n'.join(body), quote=False)))
            continue
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (level, _inline(m.group(2)), level))
            i += 1
            continue
        if _HR.match(line):
            out.append('<hr>')
            i += 1
            continue
        if _QUOTE.match(line):
            quoted = []
            while i < n and _QUOTE.match(lines[i]):
                quoted.append(re.sub(r'^\s{0,3}> ?', '', lines[i]))
                i += 1
            out.append('<blockquote>\n%s\n</blockquote>' % _blocks(quoted, depth + 1))
            continue
        nxt = lines[i + 1] if i + 1 < n else ''
        if _is_table(line, nxt):
            head = _cells(line)
            aligns = []
            for c in _cells(nxt):
                aligns.append(' style="text-align:center"' if c.startswith(':') and c.endswith(':') else ' style="text-align:right"' if c.endswith(':') else '')
            i += 2
            rows = []
            while i < n and lines[i].strip() and '|' in lines[i]:
                cells = _cells(lines[i])[:len(head)]
                rows.append(cells + [''] * (len(head) - len(cells)))
                i += 1
            th = ''.join('<th%s>%s</th>' % (aligns[k], _inline(c)) for k, c in enumerate(head))
            tr = ''.join('<tr>%s</tr>\n' % ''.join('<td%s>%s</td>' % (aligns[k], _inline(c)) for k, c in enumerate(r)) for r in rows)
            out.append('<table>\n<thead><tr>%s</tr></thead>\n<tbody>\n%s</tbody>\n</table>' % (th, tr))
            continue
        if _LIST.match(line):
            html, i = _list(lines, i, depth)
            out.append(html)
            continue
        para = [line.lstrip()]
        i += 1
        heading = 0
        while i < n and lines[i].strip():
            s = _SETEXT.match(lines[i])
            if s:
                heading = 1 if s.group(1)[0] == '=' else 2
                i += 1
                break
            if _starts_block(lines[i], lines[i + 1] if i + 1 < n else ''):
                break
            para.append(lines[i].lstrip())
            i += 1
        if heading:
            out.append('<h%d>%s</h%d>' % (heading, _inline(' '.join(p.strip() for p in para)), heading))
        else:
            out.append('<p>%s</p>' % _inline('\n'.join(para).strip()))
    return '\n'.join(out)


def render(md):
    """Markdown -> safe HTML. If the text is too deeply nested to render, it is shown as plain preformatted text instead."""
    lines = [l[:LINE_MAX] for l in str(md).replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    try:
        return _blocks(lines)
    except RecursionError:
        return '<pre>%s</pre>' % esc('\n'.join(lines), quote=False)


def plain_text(md):
    """The text of a Markdown document without its marks, for a summary or a word count."""
    t = re.sub(r'```.*?```', ' ', str(md), flags=re.S)
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', t)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)', '', t, flags=re.M)
    t = re.sub(r'[`*_~|]+', '', t)
    return re.sub(r'\s+', ' ', t).strip()
