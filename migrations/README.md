# Operations migrations

For an existing incomplete schema, use **only**
`004_ops_complete_repair.sql`. It creates missing tables, fields, indexes and
default management modules while preserving existing data. It supersedes the
older incremental `002`/`003` scripts:

```sql
\i migrations/004_ops_complete_repair.sql
```

The sync button then uses stable directory identifiers to update source
metadata without changing editorial decisions held in the `ops` tables.

To enable Operations → **订阅目录**, run this additive migration afterwards:

```sql
\i migrations/005_ops_subscription_catalog.sql
```

It keeps collected RSS candidates separate from already-published programs.

For Vercel, set `OPS_DATABASE_URL` to the PostgreSQL connection URL (or leave
it unset when the project already provides `POSTGRES_URL`). Deploying installs
the required PostgreSQL driver from `requirements.txt`.

To keep the chapters of the picked books on the server (the long-term layer of `GET /api/book`, see README B6c), run this once as well. It only creates a schema `library` with one table; it
does not touch `ops` or `public`:

```sql
\i migrations/006_library_book_chapters.sql
```

Then `python bookapi.py check-db` (the address from this terminal, or from `.env.local` / `.env` of the project folder, or `--env-file <file>`) says step by step what is missing: the address, the driver (`pip install "psycopg[binary]"`), the connection, the table; at the end it writes one row, reads it back and deletes it. `python bookapi.py load [--only <book id>]` then reads the chapters of the books that may be kept straight into the table.
