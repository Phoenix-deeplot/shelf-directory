"""Public anonymous experiment API and authenticated operations helpers."""
import datetime as dt
import hashlib
import hmac
import json
import re
import time
import uuid

from submission import Reply

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # local tests can exercise validation without PostgreSQL
    psycopg = None
    dict_row = None


EVENT_NAMES = {"experiment_exposure", "item_impression", "item_click", "active_time"}
EVENT_FIELDS = {
    "event_id", "event_name", "occurred_at", "experiment_id", "variant", "anon_id",
    "session_id", "module_slug", "content_key", "program_id", "platform_id", "locale",
    "country_code", "device_class", "active_seconds",
}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
CONTENT_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9:_-]{0,119}$")
SOURCE_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$")
LOCALE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,2}$")
_RATE_BUCKETS = {}
_RATE_LIMIT = 120
_RATE_WINDOW = 60
_MAX_BODY = 64_000
_MAX_BATCH = 50


def database_url(env):
    return str((env or {}).get("OPS_DATABASE_URL") or (env or {}).get("POSTGRES_URL") or (env or {}).get("DATABASE_URL") or "").strip()


def _secret(env):
    return str((env or {}).get("ANALYTICS_HASH_SECRET") or (env or {}).get("ADMIN_SESSION_SECRET") or "").encode("utf-8")


def _connect(env):
    if not database_url(env) or psycopg is None:
        raise Reply(503, "database", "分析数据库尚未配置", "Analytics database is not configured")
    try:
        return psycopg.connect(database_url(env), connect_timeout=6, autocommit=True, row_factory=dict_row)
    except Exception:
        raise Reply(503, "database", "分析数据库暂不可用", "Analytics database is unavailable")


def _schema(cur):
    needed = {
        "analytics_experiments": {"experiment_id", "name", "status", "allocation", "variants", "starts_at", "ends_at", "updated_at"},
        "analytics_events": {"event_id", "occurred_at", "experiment_id", "variant", "anon_id_hash", "session_id", "event_name", "module_slug", "content_key", "program_id", "platform_id", "locale", "country_code", "device_class", "active_seconds"},
    }
    cur.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='ops' AND table_name=ANY(%s)", (list(needed),))
    found = {}
    for row in cur.fetchall():
        found.setdefault(row["table_name"], set()).add(row["column_name"])
    missing = {table: sorted(cols - found.get(table, set())) for table, cols in needed.items() if cols - found.get(table, set())}
    if missing:
        raise Reply(503, "schema", "分析数据库结构未就绪；请应用迁移 008_hot_analytics.sql（缺少表或字段）", "Analytics schema is incomplete; apply migration 008_hot_analytics.sql")


def _json(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def public_config(experiment_id, env):
    if not SLUG.fullmatch(experiment_id or ""):
        raise Reply(400, "experiment", "实验编号不正确", "Invalid experiment id")
    with _connect(env) as con, con.cursor() as cur:
        _schema(cur)
        cur.execute("SELECT experiment_id,status,allocation,variants,starts_at,ends_at FROM ops.analytics_experiments WHERE experiment_id=%s", (experiment_id,))
        row = cur.fetchone()
    if not row or row["status"] != "active":
        return {"ok": True, "experiment": {"id": experiment_id, "status": "off", "allocation": {}, "variants": []}}
    now = dt.datetime.now(dt.timezone.utc)
    if row.get("starts_at") and row["starts_at"] > now or row.get("ends_at") and row["ends_at"] <= now:
        return {"ok": True, "experiment": {"id": experiment_id, "status": "off", "allocation": {}, "variants": []}}
    variants = _json(row.get("variants"), [])
    allocation = _json(row.get("allocation"), {})
    if not isinstance(variants, list) or not isinstance(allocation, dict):
        raise Reply(503, "schema", "实验配置格式不正确", "Experiment configuration is invalid")
    public_variants = [{"id": str(item.get("id")), "label": str(item.get("label") or item.get("id"))} for item in variants if isinstance(item, dict) and item.get("id")]
    return {"ok": True, "experiment": {"id": row["experiment_id"], "status": "active", "allocation": allocation, "variants": public_variants}}


def _valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _clean_event(raw):
    if not isinstance(raw, dict) or set(raw) - EVENT_FIELDS:
        return None
    if not _valid_uuid(raw.get("event_id")) or not _valid_uuid(raw.get("anon_id")) or not _valid_uuid(raw.get("session_id")):
        return None
    name = raw.get("event_name")
    experiment = raw.get("experiment_id")
    variant = raw.get("variant")
    if name not in EVENT_NAMES or not SLUG.fullmatch(str(experiment or "")) or not SLUG.fullmatch(str(variant or "")):
        return None
    stamp = raw.get("occurred_at")
    if not isinstance(stamp, str) or len(stamp) > 40:
        return None
    try:
        occurred = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            return None
        occurred = occurred.astimezone(dt.timezone.utc)
    except ValueError:
        return None
    active = raw.get("active_seconds", 0)
    if isinstance(active, bool) or not isinstance(active, int) or not 0 <= active <= 900:
        return None
    device = raw.get("device_class")
    if device not in (None, "desktop", "mobile", "tablet", "other"):
        return None
    country = raw.get("country_code")
    if country is not None and (not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country)):
        return None
    locale = raw.get("locale")
    if locale is not None and (not isinstance(locale, str) or not LOCALE.fullmatch(locale)):
        return None
    out = {}
    for key, limit, pattern in (("module_slug", 80, SLUG), ("content_key", 120, CONTENT_KEY),
                                ("program_id", 100, SOURCE_KEY), ("platform_id", 80, SOURCE_KEY)):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str) or not pattern.fullmatch(value) or len(value) > limit:
                return None
        out[key] = value
    return {
        "event_id": raw["event_id"], "event_name": name, "occurred_at": occurred,
        "experiment_id": experiment, "variant": variant, "anon_id": raw["anon_id"],
        "session_id": raw["session_id"], **out,
        "locale": locale, "country_code": country.upper() if country else None,
        "device_class": device, "active_seconds": active,
    }


def _allowed_origin(origin, env):
    origin = (origin or "").rstrip("/")
    allowed = {x.strip().rstrip("/") for x in str((env or {}).get("ALLOWED_ORIGINS") or "https://books.zhitree.cn").split(",") if x.strip()}
    return bool(origin and origin in allowed)


def _limited(key, now):
    stale = now - _RATE_WINDOW
    for k, bucket in list(_RATE_BUCKETS.items()):
        if bucket[0] <= stale:
            del _RATE_BUCKETS[k]
    started, count = _RATE_BUCKETS.get(key, (now, 0))
    if now - started >= _RATE_WINDOW:
        started, count = now, 0
    if count >= _RATE_LIMIT:
        _RATE_BUCKETS[key] = (started, count)
        return True
    _RATE_BUCKETS[key] = (started, count + 1)
    return False


def ingest_events(payload, env, source="", now=None):
    now = time.time() if now is None else now
    if not _secret(env):
        raise Reply(503, "config", "分析摘要密钥尚未配置", "Analytics hashing secret is not configured")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list) or not 1 <= len(payload["events"]) <= _MAX_BATCH:
        raise Reply(400, "request", "events 必须包含 1–50 条事件", "events must contain 1 to 50 records")
    source_key = hmac.new(_secret(env), (source or "unknown").encode("utf-8"), hashlib.sha256).hexdigest()
    if _limited(source_key, now):
        raise Reply(429, "rate_limit", "请求太频繁，请稍后重试", "Rate limit exceeded")
    cleaned = [_clean_event(event) for event in payload["events"]]
    accepted, rejected = 0, sum(item is None for item in cleaned)
    batch = [x for x in cleaned if x is not None]
    if not batch:
        return {"ok": True, "accepted": 0, "rejected": rejected}
    with _connect(env) as con, con.cursor() as cur:
        _schema(cur)
        ids = {item["experiment_id"] for item in batch}
        cur.execute("SELECT experiment_id,status,variants,starts_at,ends_at FROM ops.analytics_experiments WHERE experiment_id=ANY(%s)", (list(ids),))
        experiments = {row["experiment_id"]: row for row in cur.fetchall()}
        for item in batch:
            anon_key = hmac.new(_secret(env), item["anon_id"].encode("utf-8"), hashlib.sha256).hexdigest()
            if _limited("anon:" + anon_key, now):
                rejected += 1
                continue
            exp = experiments.get(item["experiment_id"])
            variants = _json(exp.get("variants"), []) if exp else []
            allowed = {str(v.get("id")) for v in variants if isinstance(v, dict)}
            if not exp or exp["status"] != "active" or item["variant"] not in allowed:
                rejected += 1
                continue
            anon_hash = anon_key
            cur.execute("""INSERT INTO ops.analytics_events
                (event_id,occurred_at,experiment_id,variant,anon_id_hash,session_id,event_name,module_slug,content_key,program_id,platform_id,locale,country_code,device_class,active_seconds)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING""",
                (item["event_id"], item["occurred_at"], item["experiment_id"], item["variant"], anon_hash,
                 item["session_id"], item["event_name"], item["module_slug"], item["content_key"], item["program_id"],
                 item["platform_id"], item["locale"], item["country_code"], item["device_class"], item["active_seconds"]))
            accepted += cur.rowcount
            if cur.rowcount == 0:
                rejected += 1
    return {"ok": True, "accepted": accepted, "rejected": rejected}


def handle_public(method, query, payload, origin, source, env, body_size=0):
    if not _allowed_origin(origin, env):
        raise Reply(403, "origin", "这个来源不允许", "Origin is not allowed")
    if method == "GET" and query.get("config"):
        return public_config(query["config"], env)
    if method == "POST" and query.get("events"):
        if body_size > _MAX_BODY:
            raise Reply(413, "size", "内容太大", "Request body is too large")
        return ingest_events(payload, env, source)
    raise Reply(404, "route", "找不到分析接口", "Analytics endpoint not found")


def _admin_config(cur, payload):
    eid = str(payload.get("experiment_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    status = str(payload.get("status") or "draft")
    variants = payload.get("variants")
    allocation = payload.get("allocation")
    if not SLUG.fullmatch(eid) or not 1 <= len(name) <= 120 or status not in {"draft", "active", "paused", "off"}:
        raise Reply(400, "config", "实验名称、编号或状态不正确", "Invalid experiment name, id, or status")
    if not isinstance(variants, list) or not 2 <= len(variants) <= 8 or not isinstance(allocation, dict):
        raise Reply(400, "config", "至少需要两个变体和流量比例", "At least two variants and an allocation are required")
    clean_variants, ids = [], set()
    for item in variants:
        vid = str(item.get("id") or "") if isinstance(item, dict) else ""
        label = str(item.get("label") or "") if isinstance(item, dict) else ""
        if not SLUG.fullmatch(vid) or vid in ids or not 1 <= len(label) <= 80:
            raise Reply(400, "config", "变体编号或标签不正确", "Invalid variant id or label")
        ids.add(vid); clean_variants.append({"id": vid, "label": label})
    if set(allocation) != ids or any(type(v) not in (int, float) or v < 0 or v > 100 for v in allocation.values()) or abs(sum(allocation.values()) - 100) > .01:
        raise Reply(400, "config", "流量比例必须匹配变体且合计 100%", "Allocation must match variants and sum to 100%")
    starts, ends = payload.get("starts_at"), payload.get("ends_at")
    for value in (starts, ends):
        if value is not None:
            try: dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, AttributeError): raise Reply(400, "config", "起止时间格式不正确", "Invalid experiment date")
    if status == "active":
        cur.execute("SELECT 1 FROM ops.analytics_experiments WHERE status='active' AND experiment_id<>%s AND (starts_at IS NULL OR starts_at<=NOW()) AND (ends_at IS NULL OR ends_at>NOW()) LIMIT 1", (eid,))
        if cur.fetchone():
            raise Reply(409, "active_experiment", "已有其他实验正在运行", "Another experiment is already active")
    cur.execute("""INSERT INTO ops.analytics_experiments(experiment_id,name,status,allocation,variants,starts_at,ends_at,updated_at)
        VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,NOW()) ON CONFLICT(experiment_id) DO UPDATE SET
        name=EXCLUDED.name,status=EXCLUDED.status,allocation=EXCLUDED.allocation,variants=EXCLUDED.variants,
        starts_at=EXCLUDED.starts_at,ends_at=EXCLUDED.ends_at,updated_at=NOW()""",
        (eid, name, status, json.dumps(allocation), json.dumps(clean_variants), starts, ends))
    return {"id": eid, "name": name, "status": status, "allocation": allocation, "variants": clean_variants, "starts_at": starts, "ends_at": ends}


def _date(value, fallback):
    if value in (None, ""):
        return fallback
    try: return dt.date.fromisoformat(str(value))
    except ValueError: raise Reply(400, "date", "日期格式应为 YYYY-MM-DD", "Dates must use YYYY-MM-DD")


def _summary(cur, payload):
    eid = str(payload.get("experiment_id") or "")
    if not SLUG.fullmatch(eid): raise Reply(400, "experiment", "实验编号不正确", "Invalid experiment id")
    today = dt.date.today()
    start, end = _date(payload.get("from"), today - dt.timedelta(days=6)), _date(payload.get("to"), today)
    if end < start or (end - start).days > 89: raise Reply(400, "range", "日期范围最多 90 天", "Date range cannot exceed 90 days")
    filters = {"locale": "locale", "device_class": "device_class", "module_slug": "module_slug"}
    where, args = ["experiment_id=%s", "occurred_at >= %s", "occurred_at < %s"], [eid, start, end + dt.timedelta(days=1)]
    for key, col in filters.items():
        value = payload.get(key)
        if value:
            if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9._/-]+", value): raise Reply(400, "filter", "筛选条件不正确", "Invalid filter")
            where.append(col + "=%s"); args.append(value)
    clause = " AND ".join(where)
    cur.execute("""WITH base AS (
          SELECT * FROM ops.analytics_events WHERE """ + clause + """),
        active_sessions AS (
          SELECT variant,anon_id_hash,session_id,SUM(active_seconds) AS seconds
          FROM base WHERE event_name='active_time' GROUP BY variant,anon_id_hash,session_id),
        metrics AS (
          SELECT variant,
            COUNT(DISTINCT anon_id_hash) FILTER (WHERE event_name IN ('experiment_exposure','item_impression')) AS exposures,
            COUNT(DISTINCT (anon_id_hash,content_key)) FILTER (WHERE event_name='item_click' AND content_key IS NOT NULL) AS clicks
          FROM base GROUP BY variant),
        active AS (
          SELECT variant,COUNT(*) AS active_samples,AVG(seconds) AS active_avg,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) AS active_median
          FROM active_sessions GROUP BY variant)
        SELECT metrics.variant,metrics.exposures,metrics.clicks,COALESCE(active.active_samples,0) active_samples,
          COALESCE(active.active_avg,0) active_avg,COALESCE(active.active_median,0) active_median
        FROM metrics LEFT JOIN active USING(variant) ORDER BY metrics.variant""", args)
    variants = []
    for row in cur.fetchall():
        exposures, clicks = int(row["exposures"] or 0), int(row["clicks"] or 0)
        variants.append({"id": row["variant"], "exposures": exposures, "clicks": clicks,
            "ctr": round(clicks / exposures, 4) if exposures else 0,
            "active_samples": int(row["active_samples"] or 0), "active_seconds_avg": round(float(row["active_avg"] or 0), 1),
            "active_seconds_median": round(float(row["active_median"] or 0), 1), "insufficient_sample": exposures < 100})
    cur.execute("""SELECT COALESCE(module_slug,'(未分类)') module_slug,COALESCE(platform_id,program_id,'(未知来源)') source,
        COUNT(*) FILTER (WHERE event_name IN ('experiment_exposure','item_impression')) impressions,
        COUNT(*) FILTER (WHERE event_name='item_click') clicks
        FROM ops.analytics_events WHERE """ + clause + " GROUP BY module_slug,source ORDER BY clicks DESC,impressions DESC LIMIT 100", args)
    breakdown = [dict(row) for row in cur.fetchall()]
    for row in breakdown:
        row["impressions"] = int(row["impressions"] or 0); row["clicks"] = int(row["clicks"] or 0)
    return {"experiment_id": eid, "from": start.isoformat(), "to": end.isoformat(), "variants": variants,
        "breakdown": breakdown, "insufficient_sample": any(x["insufficient_sample"] for x in variants),
        "definitions": {"exposures": "按匿名 ID 去重的有效曝光与展示事件", "clicks": "匿名 ID + 内容键去重的点击", "active_time": "active_time 事件秒数的均值/中位数"}}


def ops_action(cur, payload):
    _schema(cur)
    action = payload.get("action")
    if action == "analytics-config":
        if payload.get("experiment_id"):
            cur.execute("SELECT experiment_id id,name,status,allocation,variants,starts_at,ends_at,updated_at FROM ops.analytics_experiments WHERE experiment_id=%s", (payload["experiment_id"],))
            row = cur.fetchone()
            if not row: raise Reply(404, "experiment", "找不到这个实验", "Experiment not found")
            row["allocation"] = _json(row["allocation"], {}); row["variants"] = _json(row["variants"], [])
            return dict(row)
        cur.execute("SELECT experiment_id id,name,status,allocation,variants,starts_at,ends_at,updated_at FROM ops.analytics_experiments ORDER BY updated_at DESC")
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows: row["allocation"] = _json(row["allocation"], {}); row["variants"] = _json(row["variants"], [])
        return rows
    if action == "analytics-save-config":
        return _admin_config(cur, payload)
    if action == "analytics-summary":
        return _summary(cur, payload)
    raise Reply(400, "request", "不支持这个分析操作", "Unsupported analytics action")


def cleanup_expired_events(env, days=90):
    """Maintenance entry point; only deletes events older than the retention window."""
    days = max(1, min(90, int(days)))
    with _connect(env) as con, con.cursor() as cur:
        _schema(cur)
        cur.execute("DELETE FROM ops.analytics_events WHERE occurred_at < NOW() - (%s * INTERVAL '1 day')", (days,))
        return cur.rowcount

