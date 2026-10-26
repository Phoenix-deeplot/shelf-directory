# Operations migrations

`002_ops_directory_bridge.sql` is the only additional migration required by
the operations console. Run it once, after the `ops` schema/table script you
already created:

```sql
\i migrations/002_ops_directory_bridge.sql
```

It adds `ops.programs.directory_id`, a stable link to `registry/shelves.json`.
The sync button uses that identifier to update source metadata without changing
editorial decisions held in the `ops` tables.

For Vercel, set `OPS_DATABASE_URL` to the PostgreSQL connection URL (or leave
it unset when the project already provides `POSTGRES_URL`). Deploying installs
the required PostgreSQL driver from `requirements.txt`.
