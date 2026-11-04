-- Anonymous hot-list experiment configuration and interaction events.
-- Retention is enforced by analyticsapi.cleanup_expired_events(); callers must
-- invoke it from a scheduled maintenance job rather than on every request.

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.analytics_experiments (
    experiment_id text PRIMARY KEY CHECK (experiment_id ~ '^[a-z0-9][a-z0-9-]{0,79}$'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    status text NOT NULL DEFAULT 'off' CHECK (status IN ('draft','active','paused','off')),
    allocation jsonb NOT NULL DEFAULT '{}'::jsonb,
    variants jsonb NOT NULL DEFAULT '[]'::jsonb,
    starts_at timestamptz,
    ends_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(allocation)='object'),
    CHECK (jsonb_typeof(variants)='array'),
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at)
);

CREATE INDEX IF NOT EXISTS analytics_experiments_status
    ON ops.analytics_experiments(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ops.analytics_events (
    event_id text PRIMARY KEY CHECK (length(event_id) BETWEEN 8 AND 80),
    occurred_at timestamptz NOT NULL,
    experiment_id text NOT NULL,
    variant text NOT NULL,
    anon_id_hash text NOT NULL CHECK (anon_id_hash ~ '^[0-9a-f]{64}$'),
    session_id text NOT NULL,
    event_name text NOT NULL CHECK (event_name IN ('experiment_exposure','item_impression','item_click','active_time')),
    module_slug text,
    content_key text,
    program_id text,
    platform_id text,
    locale text,
    country_code char(2),
    device_class text CHECK (device_class IS NULL OR device_class IN ('desktop','mobile','tablet','other')),
    active_seconds integer NOT NULL DEFAULT 0 CHECK (active_seconds BETWEEN 0 AND 900),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analytics_events_experiment_time
    ON ops.analytics_events(experiment_id, occurred_at DESC, variant);
CREATE INDEX IF NOT EXISTS analytics_events_module_time
    ON ops.analytics_events(experiment_id, module_slug, occurred_at DESC);
CREATE INDEX IF NOT EXISTS analytics_events_content_time
    ON ops.analytics_events(experiment_id, content_key, occurred_at DESC);
CREATE INDEX IF NOT EXISTS analytics_events_anon_time
    ON ops.analytics_events(experiment_id, anon_id_hash, occurred_at DESC);

