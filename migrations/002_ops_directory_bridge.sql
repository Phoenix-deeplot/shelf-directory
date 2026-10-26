BEGIN;

-- Bind the operations database to the stable source id used by registry/shelves.json.
-- Safe after 001_ops_schema.sql has already been applied.
ALTER TABLE ops.programs
  ADD COLUMN IF NOT EXISTS directory_id TEXT;

-- A normal PostgreSQL UNIQUE index permits multiple NULLs.  Do not make this
-- partial: `ON CONFLICT (directory_id)` used by the sync must be able to infer it.
DROP INDEX IF EXISTS ops.programs_directory_id_unique;
CREATE UNIQUE INDEX IF NOT EXISTS programs_directory_id_unique
  ON ops.programs (directory_id);

COMMIT;
