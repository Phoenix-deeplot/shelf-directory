"""Password-protected operations API for the directory admin.

The public directory remains file based.  This API only reads/writes the
``ops`` PostgreSQL schema so an editor can control presentation without
rewriting the registry or exposing a database credential to the browser.
"""
import datetime as dt
import json
import re
import time
import urllib.parse

import adminapi
import aggregate as agg
from submission import Reply

try:  # Vercel installs this from requirements.txt; local admin still works without it.
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - deliberately useful for stdlib-only local checks
    psycopg = None
    dict_row = None


BODY_LIMIT = 24_000
PROGRAM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MODULES = {"featured", "selected", "personalized", "trending", "latest"}
STATUSES = {"fixed", "dynamic", "trial", "paused"}
COVER_ENGINES = {"canvas", "css", "static"}


def _database_url(env):
    return str(env.get("OPS_DATABASE_URL") or env.get("POSTGRES_URL") or env.get("DATABASE_URL") or "").strip()


def _reply(status, code, zh, en=""):
    raise Reply(status, code, zh, en or zh)


def _check(payload, bearer, env, now):
    cfg = adminapi.settings(env)
    if not adminapi.configured(cfg):
        _reply(503, "not_configured", "管理密码服务还没有配置好", "Admin password service is not configured")
    if not adminapi.check_session(bearer, cfg, now):
        _reply(401, "session", "登录已过期，请重新输入密码", "Signed out; enter the password again")
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        _reply(400, "request", "请求格式不对", "Bad request")
    if not _database_url(env) or psycopg is None:
        _reply(503, "database", "运营数据库尚未连接：在 Vercel 配置 OPS_DATABASE_URL 后重新部署", "Set OPS_DATABASE_URL and redeploy")
    return cfg


def _connect(env):
    try:
        return psycopg.connect(_database_url(env), connect_timeout=8, autocommit=True, row_factory=dict_row)
    except Exception as exc:
        _reply(503, "database", "暂时连不上运营数据库", "Could not connect to the operations database: %s" % type(exc).__name__)


def _json(value, fallback=None):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback if fallback is not None else {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def _rows(cur):
    return [dict(row) for row in cur.fetchall()]


def _clean_bool(value):
    return value is True


def _clean_int(value, low, high, default=0):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _directory_payload(cfg):
    """Read source data through the same restricted GitHub credential as admin."""
    root = "/repos/%s/contents/" % cfg["repo"]
    st, registry = agg.gh_call("GET", root + "registry/shelves.json", token=cfg["token"], accept="application/vnd.github.raw+json", limit=8 * 1024 * 1024)
    if st != 200 or not isinstance(registry, dict):
        _reply(502, "directory", "读不到 registry/shelves.json", "Could not read registry/shelves.json")
    st, directory = agg.gh_call("GET", root + "docs/directory.json", token=cfg["token"], accept="application/vnd.github.raw+json", limit=8 * 1024 * 1024)
    if st != 200 or not isinstance(directory, dict):
        _reply(502, "directory", "读不到 docs/directory.json", "Could not read docs/directory.json")
    return registry, directory


def _source_kind(value):
    return {"feed": "rss", "repo": "web", "link": "web", "post": "manual", "record": "manual"}.get(str(value), "web")


def _url(value, fallback):
    value = str(value or "").strip()
    if value.startswith(("https://", "http://")):
        return value
    return fallback


def sync_directory(cur, cfg):
    """Import only stable identifiers and editorial-neutral metadata.

    Registry remains source-of-truth for source mechanics.  Existing editorial
    flags in ops are intentionally not overwritten by this sync.
    """
    registry, directory = _directory_payload(cfg)
    shelves = [x for x in registry.get("shelves", []) if isinstance(x, dict) and PROGRAM_ID.fullmatch(str(x.get("id") or ""))]
    topic_names = {}
    for topic in directory.get("topics", []) if isinstance(directory.get("topics"), list) else []:
        if isinstance(topic, dict) and SLUG.fullmatch(str(topic.get("id") or "")):
            topic_names[topic["id"]] = (str(topic.get("zh") or topic["id"]), str(topic.get("en") or ""))
    for shelf in shelves:
        for topic in shelf.get("topics", []) if isinstance(shelf.get("topics"), list) else []:
            if SLUG.fullmatch(str(topic)):
                topic_names.setdefault(topic, (topic, ""))
    for slug, (zh, en) in topic_names.items():
        cur.execute("""INSERT INTO ops.topics (slug, name_zh, name_en)
                       VALUES (%s,%s,%s) ON CONFLICT (slug) DO UPDATE
                       SET name_zh=EXCLUDED.name_zh, name_en=EXCLUDED.name_en""", (slug, zh[:120], en[:120] or None))
    for shelf in shelves:
        sid = shelf["id"]
        source = _url(shelf.get("url") or shelf.get("source"), "https://directory.invalid/program/" + urllib.parse.quote(sid))
        status = str(shelf.get("status") or "fixed")
        status = status if status in STATUSES else "fixed"
        public = status != "paused"
        metadata = {"rights": shelf.get("rights") or "unknown", "note": shelf.get("note") or "", "starter": bool(shelf.get("starter")), "featured": bool(shelf.get("featured"))}
        cur.execute("""INSERT INTO ops.programs
              (directory_id,name,kind,homepage_url,feed_url,platform,default_language,status,public_enabled,recommend_enabled,metadata)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
              ON CONFLICT (directory_id) DO UPDATE SET
                name=EXCLUDED.name, kind=EXCLUDED.kind, homepage_url=EXCLUDED.homepage_url,
                feed_url=EXCLUDED.feed_url, platform=EXCLUDED.platform,
                status=EXCLUDED.status, metadata=EXCLUDED.metadata, updated_at=NOW()""",
            (sid, str(shelf.get("name") or sid)[:220], _source_kind(shelf.get("kind")), source,
             source if shelf.get("kind") == "feed" else None, str(shelf.get("platform") or "")[:80] or None,
             str(shelf.get("lang") or "")[:16] or None, status, public, bool(shelf.get("featured")), json.dumps(metadata, ensure_ascii=False)))
        cur.execute("SELECT id FROM ops.programs WHERE directory_id=%s", (sid,))
        program = cur.fetchone()
        if not program:
            continue
        cur.execute("DELETE FROM ops.program_topics WHERE program_id=%s", (program["id"],))
        for topic in shelf.get("topics", []) if isinstance(shelf.get("topics"), list) else []:
            cur.execute("""INSERT INTO ops.program_topics (program_id, topic_id)
                           SELECT %s,id FROM ops.topics WHERE slug=%s
                           ON CONFLICT DO NOTHING""", (program["id"], str(topic)))
    id_to_program = {}
    cur.execute("SELECT id,directory_id FROM ops.programs WHERE directory_id IS NOT NULL")
    for row in cur.fetchall():
        id_to_program[row["directory_id"]] = row["id"]
    contents = directory.get("items", []) if isinstance(directory.get("items"), list) else []
    content_count = 0
    for item in contents:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        program_id = id_to_program.get(str(item.get("shelf") or item.get("shelf_id") or ""))
        canonical = _url(item.get("url"), "")
        if not program_id or not canonical:
            continue
        external_date = str(item.get("date") or "").strip()
        published = external_date + "T00:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", external_date) else None
        cur.execute("""INSERT INTO ops.content_items
              (content_key,program_id,canonical_url,title,summary,kind,language,cover_url,external_published_at,state,metadata)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'published',%s::jsonb)
              ON CONFLICT (content_key) DO UPDATE SET
                program_id=EXCLUDED.program_id, canonical_url=EXCLUDED.canonical_url, title=EXCLUDED.title,
                summary=EXCLUDED.summary, kind=EXCLUDED.kind, language=EXCLUDED.language,
                cover_url=EXCLUDED.cover_url, external_published_at=EXCLUDED.external_published_at,
                metadata=EXCLUDED.metadata, updated_at=NOW()""",
            ("dir:" + str(item["id"]), program_id, canonical, str(item.get("title") or "未命名")[:500],
             str(item.get("summary") or item.get("excerpt") or "")[:3000] or None,
             str(item.get("type") or "article")[:32], str(item.get("language") or item.get("lang") or "")[:16] or None,
             _url(item.get("image") or item.get("cover"), "") or None, published,
             json.dumps({"directory_id": item.get("id"), "tags": item.get("tags") or []}, ensure_ascii=False)))
        content_count += 1
    return {"programs": len(shelves), "contents": content_count, "topics": len(topic_names)}


def dashboard(cur):
    cur.execute("""SELECT COALESCE(SUM(opens),0) opens, COALESCE(SUM(saves),0) saves,
                         COALESCE(SUM(likes),0) likes, COALESCE(SUM(shares),0) shares
                  FROM ops.content_metrics_daily WHERE metric_date >= CURRENT_DATE - 6""")
    metrics = dict(cur.fetchone() or {})
    cur.execute("""SELECT name, directory_id, status, consecutive_failures, last_crawled_at
                  FROM ops.programs WHERE status='paused' OR consecutive_failures >= 3
                  ORDER BY consecutive_failures DESC, updated_at DESC LIMIT 12""")
    alerts = _rows(cur)
    cur.execute("SELECT COUNT(*) AS count FROM ops.content_items WHERE state='pending'")
    pending = int((cur.fetchone() or {}).get("count", 0))
    return {"metrics": metrics, "pending": pending, "alerts": alerts}


def get_programs(cur):
    cur.execute("""SELECT p.id,p.directory_id,p.name,p.kind,p.platform,p.status,p.public_enabled,p.recommend_enabled,
                 p.editorial_weight,p.last_crawled_at,p.consecutive_failures,
                 COALESCE(SUM(m.opens),0) opens, COALESCE(SUM(m.saves),0) saves, COALESCE(SUM(m.likes),0) likes
                 FROM ops.programs p LEFT JOIN ops.program_metrics_daily m
                 ON m.program_id=p.id AND m.metric_date >= CURRENT_DATE - 29
                 GROUP BY p.id ORDER BY p.recommend_enabled DESC,p.editorial_weight DESC,p.name""")
    return _rows(cur)


def get_modules(cur):
    cur.execute("SELECT id,slug,name,enabled,device,limit_count,source_scope,ranking_preset_id,config FROM ops.channel_modules ORDER BY sort_order,slug")
    rows = _rows(cur)
    for row in rows:
        row["config"] = _json(row.get("config"))
    return rows


def get_covers(cur):
    cur.execute("SELECT id,slug,name,render_engine,render_config,is_active FROM ops.cover_presets ORDER BY is_active DESC,slug")
    rows = _rows(cur)
    for row in rows:
        row["render_config"] = _json(row.get("render_config"))
    return rows


def get_ads(cur):
    cur.execute("SELECT id,slug,name,enabled,page_scope,device,placement,after_item,provider,slot,config FROM ops.ad_placements ORDER BY slug")
    rows = _rows(cur)
    for row in rows:
        row["config"] = _json(row.get("config"))
    return rows


def get_contents(cur):
    cur.execute("""SELECT c.id,c.content_key,c.title,c.language,c.kind,c.cover_url,c.external_published_at,
                p.name program_name,p.directory_id,COALESCE(o.editorial_weight,0) editorial_weight,
                COALESCE(o.hidden,false) hidden,o.cover_mode,o.cover_preset_id,o.editorial_note,
                COALESCE((SELECT jsonb_agg(cm.slug ORDER BY cm.sort_order)
                 FROM ops.content_placements cp JOIN ops.channel_modules cm ON cm.id=cp.module_id
                 WHERE cp.content_id=c.id), '[]'::jsonb) placement_slugs,
                COALESCE(SUM(m.opens),0) opens,COALESCE(SUM(m.saves),0) saves,COALESCE(SUM(m.likes),0) likes
                FROM ops.content_items c JOIN ops.programs p ON p.id=c.program_id
                LEFT JOIN ops.content_overrides o ON o.content_id=c.id
                LEFT JOIN ops.content_metrics_daily m ON m.content_id=c.id AND m.metric_date >= CURRENT_DATE-29
                WHERE c.state='published' GROUP BY c.id,p.name,p.directory_id,o.id
                ORDER BY COALESCE(SUM(m.opens),0) DESC,c.external_published_at DESC NULLS LAST LIMIT 80""")
    rows = _rows(cur)
    for row in rows:
        row["placement_slugs"] = _json(row.get("placement_slugs"), [])
    return rows


def _save_program(cur, payload):
    pid = str(payload.get("id") or "")
    if not re.fullmatch(r"[0-9a-f-]{36}", pid):
        _reply(400, "program", "节目编号不正确")
    status = str(payload.get("status") or "")
    if status not in STATUSES:
        _reply(400, "program", "节目状态不正确")
    weight = _clean_int(payload.get("editorial_weight"), -30, 30)
    cur.execute("""UPDATE ops.programs SET status=%s, public_enabled=%s,recommend_enabled=%s,
                   editorial_weight=%s,updated_at=NOW() WHERE id=%s""",
                (status, _clean_bool(payload.get("public_enabled")), _clean_bool(payload.get("recommend_enabled")), weight, pid))
    if cur.rowcount != 1:
        _reply(404, "program", "找不到这个节目")


def _save_module(cur, payload):
    slug = str(payload.get("slug") or "")
    if slug not in MODULES:
        _reply(400, "module", "栏目编号不正确")
    device = str(payload.get("device") or "all")
    if device not in {"all", "desktop", "mobile"}:
        _reply(400, "module", "设备范围不正确")
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    cur.execute("""UPDATE ops.channel_modules SET enabled=%s,device=%s,limit_count=%s,
                   source_scope=%s::jsonb,config=%s::jsonb,updated_at=NOW() WHERE slug=%s""",
                (_clean_bool(payload.get("enabled")), device, _clean_int(payload.get("limit_count"), 1, 24, 6),
                 json.dumps(payload.get("source_scope") if isinstance(payload.get("source_scope"), dict) else {}, ensure_ascii=False),
                 json.dumps(config, ensure_ascii=False), slug))
    if cur.rowcount != 1:
        _reply(404, "module", "找不到这个栏目")


def _save_cover(cur, payload):
    slug = str(payload.get("slug") or "")
    name = str(payload.get("name") or "").strip()
    engine = str(payload.get("render_engine") or "")
    config = payload.get("render_config")
    if not SLUG.fullmatch(slug) or not name or len(name) > 80 or engine not in COVER_ENGINES or not isinstance(config, dict):
        _reply(400, "cover", "动态封面参数不正确")
    cur.execute("""INSERT INTO ops.cover_presets(slug,name,render_engine,render_config,is_active)
                   VALUES(%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT(slug) DO UPDATE SET name=EXCLUDED.name,render_engine=EXCLUDED.render_engine,
                   render_config=EXCLUDED.render_config,is_active=EXCLUDED.is_active,updated_at=NOW()""",
                (slug, name, engine, json.dumps(config, ensure_ascii=False), _clean_bool(payload.get("is_active"))))


def _save_ad(cur, payload):
    slug = str(payload.get("slug") or "")
    if not SLUG.fullmatch(slug):
        _reply(400, "ad", "广告位编号不正确")
    placement = str(payload.get("placement") or "")
    if placement not in {"sidebar", "infeed", "reader-bottom"}:
        _reply(400, "ad", "广告位置不正确")
    device = str(payload.get("device") or "all")
    if device not in {"all", "desktop", "mobile"}:
        _reply(400, "ad", "设备范围不正确")
    cur.execute("""UPDATE ops.ad_placements SET enabled=%s,device=%s,placement=%s,after_item=%s,
                  config=%s::jsonb,updated_at=NOW() WHERE slug=%s""",
        (_clean_bool(payload.get("enabled")), device, placement, _clean_int(payload.get("after_item"), 0, 30),
         json.dumps(payload.get("config") if isinstance(payload.get("config"), dict) else {}, ensure_ascii=False), slug))
    if cur.rowcount != 1:
        _reply(404, "ad", "找不到这个广告位")


def _save_content(cur, payload):
    cid = str(payload.get("id") or "")
    if not re.fullmatch(r"[0-9a-f-]{36}", cid):
        _reply(400, "content", "内容编号不正确")
    mode = str(payload.get("cover_mode") or "auto")
    if mode not in {"auto", "source", "preset", "none"}:
        _reply(400, "content", "封面模式不正确")
    preset = str(payload.get("cover_preset_id") or "").strip() or None
    note = str(payload.get("editorial_note") or "").strip()
    if len(note) > 160:
        _reply(400, "content", "推荐理由最多 160 个字")
    cur.execute("""INSERT INTO ops.content_overrides(content_id,editorial_weight,hidden,cover_mode,cover_preset_id,editorial_note)
                   VALUES(%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(content_id) DO UPDATE SET editorial_weight=EXCLUDED.editorial_weight,
                   hidden=EXCLUDED.hidden,cover_mode=EXCLUDED.cover_mode,cover_preset_id=EXCLUDED.cover_preset_id,
                   editorial_note=EXCLUDED.editorial_note,updated_at=NOW()""",
                (cid, _clean_int(payload.get("editorial_weight"), -30, 30), _clean_bool(payload.get("hidden")), mode, preset, note or None))
    modules = payload.get("placement_modules")
    if modules is not None:
        if not isinstance(modules, list) or len(modules) > len(MODULES) or any(x not in MODULES for x in modules):
            _reply(400, "placement", "内容投放栏目不正确")
        cur.execute("DELETE FROM ops.content_placements WHERE content_id=%s", (cid,))
        for slug in dict.fromkeys(modules):
            cur.execute("""INSERT INTO ops.content_placements(content_id,module_id,priority,reason)
                           SELECT %s,id,0,'manual' FROM ops.channel_modules WHERE slug=%s""", (cid, slug))


def handle(payload, bearer, ip, env, now=None):
    now = now if now is not None else time.time()
    try:
        cfg = _check(payload, bearer, env, now)
        action = payload["action"]
        with _connect(env) as con, con.cursor() as cur:
            if action == "dashboard":
                return 200, {"ok": True, "data": dashboard(cur)}
            if action == "sync_directory":
                return 200, {"ok": True, "data": sync_directory(cur, cfg)}
            if action == "programs":
                return 200, {"ok": True, "data": get_programs(cur)}
            if action == "modules":
                return 200, {"ok": True, "data": get_modules(cur)}
            if action == "covers":
                return 200, {"ok": True, "data": get_covers(cur)}
            if action == "ads":
                return 200, {"ok": True, "data": get_ads(cur)}
            if action == "contents":
                return 200, {"ok": True, "data": get_contents(cur)}
            if action == "save_program":
                _save_program(cur, payload)
            elif action == "save_module":
                _save_module(cur, payload)
            elif action == "save_cover":
                _save_cover(cur, payload)
            elif action == "save_ad":
                _save_ad(cur, payload)
            elif action == "save_content":
                _save_content(cur, payload)
            else:
                _reply(400, "request", "不支持这个运营操作", "Unsupported operations action")
            return 200, {"ok": True}
    except Reply as reply:
        return reply.status, reply.body()
    except Exception as exc:
        # Database messages can expose SQL/table details; keep them in function logs only.
        print("opsapi error:", type(exc).__name__, str(exc))
        return 500, {"ok": False, "error": "server", "message": "运营服务暂时出错，请稍后再试", "message_en": "Operations service error"}


def respond(h, env, method):
    def send(status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    if method == "GET":
        return send(200, {"ok": True, "service": "shelf-ops", "configured": bool(_database_url(env) and psycopg is not None)})
    try:
        length = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        length = -1
    if method != "POST":
        return send(405, {"ok": False, "error": "method", "message": "不支持"})
    if length < 0 or length > BODY_LIMIT:
        return send(413, {"ok": False, "error": "size", "message": "内容太大"})
    origin = h.headers.get("Origin") or ""
    host = (h.headers.get("X-Forwarded-Host") or h.headers.get("Host") or "").split(",")[0].strip().lower()
    if origin and urllib.parse.urlsplit(origin).netloc.lower() != host:
        return send(403, {"ok": False, "error": "origin", "message": "这个来源不允许"})
    try:
        payload = json.loads(h.rfile.read(length).decode("utf-8")) if length else None
    except ValueError:
        payload = None
    auth = h.headers.get("Authorization") or ""
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    ip = (h.headers.get("X-Vercel-Forwarded-For") or h.headers.get("X-Forwarded-For") or (h.client_address[0] if h.client_address else "")).split(",")[0].strip()
    status, body = handle(payload, bearer, ip, env)
    return send(status, body)
