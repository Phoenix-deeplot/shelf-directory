-- Subscription collection: candidates can be tested before publication.
-- Additive and safe after 004_ops_complete_repair.sql.

BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.subscription_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  name_en TEXT,
  description TEXT,
  description_en TEXT,
  homepage_url TEXT NOT NULL,
  feed_url TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL DEFAULT 'native',
  platform TEXT,
  category TEXT NOT NULL DEFAULT 'other',
  language TEXT,
  topics JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'trial',
  recommend_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  public_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT subscription_candidates_type_check CHECK (source_type IN ('native','rsshub','rssapp')),
  CONSTRAINT subscription_candidates_status_check CHECK (status IN ('trial','active','paused')),
  CONSTRAINT subscription_candidates_category_check CHECK (category IN ('hot','tech','study','business','world','audio','books','other')),
  CONSTRAINT subscription_candidates_language_check CHECK (language IS NULL OR language IN ('zh','en','multi'))
);

CREATE INDEX IF NOT EXISTS subscription_candidates_catalog_idx
  ON ops.subscription_candidates (recommend_enabled, public_enabled, category, updated_at DESC);

DROP TRIGGER IF EXISTS trg_subscription_candidates_updated_at ON ops.subscription_candidates;
CREATE TRIGGER trg_subscription_candidates_updated_at
  BEFORE UPDATE ON ops.subscription_candidates
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
COMMIT;
