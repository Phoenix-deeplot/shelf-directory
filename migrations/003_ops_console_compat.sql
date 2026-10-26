BEGIN;

-- Compatibility bridge for the earlier ops schema.  It is additive: existing
-- data is left untouched.  These fields are used by /api/ops and the 运营 tab.
ALTER TABLE ops.programs
  ADD COLUMN IF NOT EXISTS directory_id TEXT,
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'web',
  ADD COLUMN IF NOT EXISTS homepage_url TEXT,
  ADD COLUMN IF NOT EXISTS feed_url TEXT,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'fixed',
  ADD COLUMN IF NOT EXISTS platform TEXT,
  ADD COLUMN IF NOT EXISTS default_language TEXT,
  ADD COLUMN IF NOT EXISTS public_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS recommend_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS editorial_weight INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_crawled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE ops.content_items
  ADD COLUMN IF NOT EXISTS program_id UUID,
  ADD COLUMN IF NOT EXISTS canonical_url TEXT,
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'article',
  ADD COLUMN IF NOT EXISTS language TEXT,
  ADD COLUMN IF NOT EXISTS cover_url TEXT,
  ADD COLUMN IF NOT EXISTS external_published_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'published',
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE ops.content_overrides
  ADD COLUMN IF NOT EXISTS editorial_weight INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS cover_mode TEXT NOT NULL DEFAULT 'auto',
  ADD COLUMN IF NOT EXISTS cover_preset_id UUID,
  ADD COLUMN IF NOT EXISTS editorial_note TEXT;

ALTER TABLE ops.channel_modules
  ADD COLUMN IF NOT EXISTS slug TEXT,
  ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS device TEXT NOT NULL DEFAULT 'all',
  ADD COLUMN IF NOT EXISTS limit_count INTEGER NOT NULL DEFAULT 6,
  ADD COLUMN IF NOT EXISTS source_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS ranking_preset_id UUID,
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

-- Earlier rows have names but no stable key.  Give each one a deterministic,
-- valid key before the uniqueness index is installed.
UPDATE ops.channel_modules
SET slug = CASE
  WHEN lower(name) LIKE '%精选%' THEN 'selected'
  WHEN lower(name) LIKE '%推荐%' THEN 'personalized'
  WHEN lower(name) LIKE '%热点%' OR lower(name) LIKE '%正在发生%' THEN 'trending'
  WHEN lower(name) LIKE '%最新%' THEN 'latest'
  ELSE 'module-' || substr(replace(id::text, '-', ''), 1, 12)
END
WHERE slug IS NULL OR btrim(slug) = '';

ALTER TABLE ops.cover_presets ADD COLUMN IF NOT EXISTS slug TEXT;
ALTER TABLE ops.cover_presets ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE ops.cover_presets
SET slug = 'cover-' || substr(replace(id::text, '-', ''), 1, 12)
WHERE slug IS NULL OR btrim(slug) = '';

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
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE ops.ad_placements
SET slug = 'ad-' || substr(replace(id::text, '-', ''), 1, 12)
WHERE slug IS NULL OR btrim(slug) = '';

-- PostgreSQL UNIQUE permits several NULLs.  These non-partial indexes are
-- needed for INSERT ... ON CONFLICT (slug) in the operations API.
CREATE UNIQUE INDEX IF NOT EXISTS programs_directory_id_unique
  ON ops.programs (directory_id);
CREATE UNIQUE INDEX IF NOT EXISTS channel_modules_slug_unique
  ON ops.channel_modules (slug);
CREATE UNIQUE INDEX IF NOT EXISTS cover_presets_slug_unique
  ON ops.cover_presets (slug);
CREATE UNIQUE INDEX IF NOT EXISTS ad_placements_slug_unique
  ON ops.ad_placements (slug);

COMMIT;
