BEGIN;

-- Bind the operations database to the stable source id used by registry/shelves.json.
-- Safe after 001_ops_schema.sql has already been applied.
ALTER TABLE ops.programs
  ADD COLUMN IF NOT EXISTS directory_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS programs_directory_id_unique
  ON ops.programs (directory_id)
  WHERE directory_id IS NOT NULL;

COMMIT;
