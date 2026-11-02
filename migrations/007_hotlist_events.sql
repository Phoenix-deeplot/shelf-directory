-- Additive storage for true daily chart snapshots and source-linked event threads.
-- This migration does not change or remove existing schemas or tables.

CREATE SCHEMA IF NOT EXISTS hot;

CREATE TABLE IF NOT EXISTS hot.chart_snapshots (
    platform_id   text        NOT NULL,
    region        text        NOT NULL DEFAULT '',
    snapshot_date date        NOT NULL,
    fetched_at    timestamptz NOT NULL,
    items         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (platform_id, region, snapshot_date)
);

CREATE INDEX IF NOT EXISTS hot_chart_snapshots_date
    ON hot.chart_snapshots (snapshot_date DESC);

CREATE TABLE IF NOT EXISTS hot.event_topics (
    id          text        PRIMARY KEY,
    title       text        NOT NULL,
    title_en    text        NOT NULL DEFAULT '',
    category    text        NOT NULL,
    is_pinned   boolean     NOT NULL DEFAULT false,
    is_ignored  boolean     NOT NULL DEFAULT false,
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE hot.event_topics ADD COLUMN IF NOT EXISTS archived_at timestamptz;

CREATE INDEX IF NOT EXISTS hot_event_topics_visibility
    ON hot.event_topics (is_ignored, is_pinned DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS hot.event_sources (
    id            bigserial   PRIMARY KEY,
    event_id      text        NOT NULL REFERENCES hot.event_topics(id) ON DELETE CASCADE,
    source_key    text        NOT NULL,
    title         text        NOT NULL,
    url           text        NOT NULL,
    published_at  timestamptz,
    excerpt       text        NOT NULL DEFAULT '',
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, url)
);

CREATE INDEX IF NOT EXISTS hot_event_sources_event_published
    ON hot.event_sources (event_id, published_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS hot_event_sources_first_seen
    ON hot.event_sources (event_id, first_seen_at DESC);

CREATE TABLE IF NOT EXISTS hot.event_refresh_state (
    singleton       boolean     PRIMARY KEY DEFAULT true CHECK (singleton),
    refreshed_at    timestamptz NOT NULL,
    candidate_count integer     NOT NULL DEFAULT 0
);
