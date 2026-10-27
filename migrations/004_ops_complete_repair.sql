-- Complete, non-destructive operations schema repair.
--
-- Use this for an existing incomplete `ops` schema. It creates missing tables
-- and only ADDs missing columns/indexes; it does not DROP tables or data.
-- Run this one script instead of manually trying 002/003 one by one.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE OR REPLACE FUNCTION ops.touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS ops.topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  name_zh TEXT NOT NULL,
  name_en TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.programs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  directory_id TEXT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'web',
  homepage_url TEXT,
  feed_url TEXT,
  platform TEXT,
  default_language TEXT,
  status TEXT NOT NULL DEFAULT 'fixed',
  public_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  recommend_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  editorial_weight INTEGER NOT NULL DEFAULT 0,
  last_crawled_at TIMESTAMPTZ,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.program_topics (
  program_id UUID NOT NULL REFERENCES ops.programs(id) ON DELETE CASCADE,
  topic_id UUID NOT NULL REFERENCES ops.topics(id) ON DELETE CASCADE,
  PRIMARY KEY (program_id, topic_id)
);

CREATE TABLE IF NOT EXISTS ops.content_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content_key TEXT NOT NULL,
  program_id UUID REFERENCES ops.programs(id) ON DELETE SET NULL,
  canonical_url TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  kind TEXT NOT NULL DEFAULT 'article',
  language TEXT,
  cover_url TEXT,
  external_published_at TIMESTAMPTZ,
  state TEXT NOT NULL DEFAULT 'published',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.content_topics (
  content_id UUID NOT NULL REFERENCES ops.content_items(id) ON DELETE CASCADE,
  topic_id UUID NOT NULL REFERENCES ops.topics(id) ON DELETE CASCADE,
  PRIMARY KEY (content_id, topic_id)
);

CREATE TABLE IF NOT EXISTS ops.cover_presets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  render_engine TEXT NOT NULL DEFAULT 'canvas',
  render_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.content_overrides (
  content_id UUID PRIMARY KEY REFERENCES ops.content_items(id) ON DELETE CASCADE,
  editorial_weight INTEGER NOT NULL DEFAULT 0,
  hidden BOOLEAN NOT NULL DEFAULT FALSE,
  cover_mode TEXT NOT NULL DEFAULT 'auto',
  cover_preset_id UUID,
  editorial_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.ranking_presets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  formula JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.channel_modules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  device TEXT NOT NULL DEFAULT 'all',
  limit_count INTEGER NOT NULL DEFAULT 6,
  source_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  ranking_preset_id UUID,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.content_placements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content_id UUID NOT NULL REFERENCES ops.content_items(id) ON DELETE CASCADE,
  module_id UUID NOT NULL REFERENCES ops.channel_modules(id) ON DELETE CASCADE,
  priority INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  starts_at TIMESTAMPTZ,
  ends_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.content_metrics_daily (
  content_id UUID NOT NULL REFERENCES ops.content_items(id) ON DELETE CASCADE,
  metric_date DATE NOT NULL,
  opens INTEGER NOT NULL DEFAULT 0,
  saves INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (content_id, metric_date)
);

CREATE TABLE IF NOT EXISTS ops.program_metrics_daily (
  program_id UUID NOT NULL REFERENCES ops.programs(id) ON DELETE CASCADE,
  metric_date DATE NOT NULL,
  opens INTEGER NOT NULL DEFAULT 0,
  saves INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (program_id, metric_date)
);

CREATE TABLE IF NOT EXISTS ops.topic_metrics_daily (
  topic_id UUID NOT NULL REFERENCES ops.topics(id) ON DELETE CASCADE,
  metric_date DATE NOT NULL,
  opens INTEGER NOT NULL DEFAULT 0,
  saves INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (topic_id, metric_date)
);

CREATE TABLE IF NOT EXISTS ops.crawler_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id UUID REFERENCES ops.programs(id) ON DELETE SET NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  items_found INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ops.ad_placements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  page_scope TEXT NOT NULL DEFAULT 'public',
  device TEXT NOT NULL DEFAULT 'all',
  placement TEXT NOT NULL DEFAULT 'infeed',
  after_item INTEGER NOT NULL DEFAULT 0,
  provider TEXT,
  slot TEXT,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.app_settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.ops_audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add every field used by the operations API when an older table already exists.
ALTER TABLE ops.programs
  ADD COLUMN IF NOT EXISTS directory_id TEXT,
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'web',
  ADD COLUMN IF NOT EXISTS homepage_url TEXT,
  ADD COLUMN IF NOT EXISTS feed_url TEXT,
  ADD COLUMN IF NOT EXISTS platform TEXT,
  ADD COLUMN IF NOT EXISTS default_language TEXT,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'fixed',
  ADD COLUMN IF NOT EXISTS public_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS recommend_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS editorial_weight INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_crawled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.content_items
  ADD COLUMN IF NOT EXISTS program_id UUID,
  ADD COLUMN IF NOT EXISTS canonical_url TEXT,
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'article',
  ADD COLUMN IF NOT EXISTS language TEXT,
  ADD COLUMN IF NOT EXISTS cover_url TEXT,
  ADD COLUMN IF NOT EXISTS external_published_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'published',
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.content_overrides
  ADD COLUMN IF NOT EXISTS editorial_weight INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS cover_mode TEXT NOT NULL DEFAULT 'auto',
  ADD COLUMN IF NOT EXISTS cover_preset_id UUID,
  ADD COLUMN IF NOT EXISTS editorial_note TEXT,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.channel_modules
  ADD COLUMN IF NOT EXISTS slug TEXT,
  ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS device TEXT NOT NULL DEFAULT 'all',
  ADD COLUMN IF NOT EXISTS limit_count INTEGER NOT NULL DEFAULT 6,
  ADD COLUMN IF NOT EXISTS source_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS ranking_preset_id UUID,
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.cover_presets
  ADD COLUMN IF NOT EXISTS slug TEXT,
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.ad_placements
  ADD COLUMN IF NOT EXISTS slug TEXT,
  ADD COLUMN IF NOT EXISTS name TEXT,
  ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS page_scope TEXT NOT NULL DEFAULT 'public',
  ADD COLUMN IF NOT EXISTS device TEXT NOT NULL DEFAULT 'all',
  ADD COLUMN IF NOT EXISTS placement TEXT NOT NULL DEFAULT 'infeed',
  ADD COLUMN IF NOT EXISTS after_item INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS provider TEXT,
  ADD COLUMN IF NOT EXISTS slot TEXT,
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ops.content_placements
  ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reason TEXT,
  ADD COLUMN IF NOT EXISTS starts_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ;

-- Populate keys for old rows before creating the keys needed by ON CONFLICT.
UPDATE ops.channel_modules SET slug = 'module-' || substr(replace(id::text, '-', ''), 1, 12)
WHERE slug IS NULL OR btrim(slug) = '';
UPDATE ops.cover_presets SET slug = 'cover-' || substr(replace(id::text, '-', ''), 1, 12)
WHERE slug IS NULL OR btrim(slug) = '';
UPDATE ops.ad_placements SET slug = 'ad-' || substr(replace(id::text, '-', ''), 1, 12)
WHERE slug IS NULL OR btrim(slug) = '';
UPDATE ops.ad_placements SET name = slug WHERE name IS NULL OR btrim(name) = '';

CREATE UNIQUE INDEX IF NOT EXISTS programs_directory_id_unique ON ops.programs(directory_id);
CREATE UNIQUE INDEX IF NOT EXISTS content_items_content_key_unique ON ops.content_items(content_key);
CREATE UNIQUE INDEX IF NOT EXISTS cover_presets_slug_unique ON ops.cover_presets(slug);
CREATE UNIQUE INDEX IF NOT EXISTS ranking_presets_slug_unique ON ops.ranking_presets(slug);
CREATE UNIQUE INDEX IF NOT EXISTS channel_modules_slug_unique ON ops.channel_modules(slug);
CREATE UNIQUE INDEX IF NOT EXISTS content_placements_content_module_unique ON ops.content_placements(content_id,module_id);
CREATE UNIQUE INDEX IF NOT EXISTS ad_placements_slug_unique ON ops.ad_placements(slug);
CREATE INDEX IF NOT EXISTS content_items_program_published_idx ON ops.content_items(program_id,external_published_at DESC);
CREATE INDEX IF NOT EXISTS content_metrics_daily_date_idx ON ops.content_metrics_daily(metric_date DESC);

-- Defaults make the management page useful immediately; none overwrite your own
-- rows or settings.
INSERT INTO ops.ranking_presets (slug,name,formula) VALUES
  ('balanced','平衡推荐','{"opens":1,"saves":4,"likes":3,"shares":5,"recency_decay_hours":72}'::jsonb),
  ('hot','热度优先','{"opens":2,"saves":3,"likes":2,"shares":5,"recency_decay_hours":36}'::jsonb),
  ('quality','收藏优先','{"opens":1,"saves":6,"likes":4,"shares":5,"recency_decay_hours":120}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO ops.channel_modules (slug,name,enabled,device,limit_count,sort_order,config) VALUES
  ('featured','今日值得看',TRUE,'all',3,10,'{}'::jsonb),
  ('selected','首页推荐',TRUE,'all',6,20,'{}'::jsonb),
  ('personalized','为你精选',TRUE,'all',6,30,'{}'::jsonb),
  ('trending','正在发生',TRUE,'all',6,40,'{}'::jsonb),
  ('latest','最新更新',TRUE,'all',12,50,'{}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO ops.cover_presets (slug,name,render_engine,render_config,is_active) VALUES
  ('particle-network','粒子网络','canvas','{"palette":["#0B4F3B","#1F7A6D","#6FB98F","#E8C778"],"particle_count_desktop":42,"particle_count_mobile":18,"speed":0.22,"connect_distance":110,"text_safe_area":"bottom","reduced_motion":"static-svg"}'::jsonb,TRUE)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO ops.ad_placements (slug,name,enabled,page_scope,device,placement,after_item,config) VALUES
  ('public-sidebar','公共空间右侧栏',FALSE,'public','desktop','sidebar',0,'{}'::jsonb),
  ('public-infeed-mobile','公共空间信息流',FALSE,'public','mobile','infeed',6,'{}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

DROP TRIGGER IF EXISTS trg_programs_updated_at ON ops.programs;
CREATE TRIGGER trg_programs_updated_at BEFORE UPDATE ON ops.programs FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
DROP TRIGGER IF EXISTS trg_content_items_updated_at ON ops.content_items;
CREATE TRIGGER trg_content_items_updated_at BEFORE UPDATE ON ops.content_items FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
DROP TRIGGER IF EXISTS trg_cover_presets_updated_at ON ops.cover_presets;
CREATE TRIGGER trg_cover_presets_updated_at BEFORE UPDATE ON ops.cover_presets FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
DROP TRIGGER IF EXISTS trg_content_overrides_updated_at ON ops.content_overrides;
CREATE TRIGGER trg_content_overrides_updated_at BEFORE UPDATE ON ops.content_overrides FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
DROP TRIGGER IF EXISTS trg_channel_modules_updated_at ON ops.channel_modules;
CREATE TRIGGER trg_channel_modules_updated_at BEFORE UPDATE ON ops.channel_modules FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
DROP TRIGGER IF EXISTS trg_ad_placements_updated_at ON ops.ad_placements;
CREATE TRIGGER trg_ad_placements_updated_at BEFORE UPDATE ON ops.ad_placements FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

COMMIT;
