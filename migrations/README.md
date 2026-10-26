# Operations migrations

Run `002_ops_directory_bridge.sql` once after the initial `ops` schema/table
script. If that initial schema came from an earlier version (the Operations
page says a column such as `slug`, `language`, or `is_active` is missing), run
`003_ops_console_compat.sql` immediately after it:

```sql
\i migrations/002_ops_directory_bridge.sql
\i migrations/003_ops_console_compat.sql
```

They add stable directory identifiers and the presentation-only fields used by
the console. The sync button uses those identifiers to update source metadata
without changing editorial decisions held in the `ops` tables.

For Vercel, set `OPS_DATABASE_URL` to the PostgreSQL connection URL (or leave
it unset when the project already provides `POSTGRES_URL`). Deploying installs
the required PostgreSQL driver from `requirements.txt`.
