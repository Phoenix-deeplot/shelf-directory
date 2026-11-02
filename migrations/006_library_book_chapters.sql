-- The long-term layer of the shared copy of the picked books' chapters (bookapi.py, GET /api/book).
-- Run it once in the database that holds the operations schema (see migrations/README.md), for example:  \i migrations/006_library_book_chapters.sql
-- It only creates a new schema "library" with one table; it does not touch "ops" or "public".
-- A chapter is kept here when its book is marked "keep": true in registry/books.json, or when it has been read often enough (BOOK_PROMOTE_READS).
-- Only books whose licence lets anybody copy them (public-domain, cc0, cc-by, cc-by-sa) are ever written here.

CREATE SCHEMA IF NOT EXISTS library;

CREATE TABLE IF NOT EXISTS library.book_chapters (
    book_id      text             NOT NULL,                      -- the id of the book in registry/books.json
    ch           integer          NOT NULL,                      -- the chapter's number (0 for a book that is one page)
    parser       integer          NOT NULL,                      -- the version of the reading (bookapi.PARSER): a better one is read again
    title        text             NOT NULL DEFAULT '',
    html         text             NOT NULL,                      -- the safe html of the chapter (what readable.py makes of the page)
    lang         text             NOT NULL DEFAULT '',
    license      text             NOT NULL,
    source_url   text             NOT NULL,                      -- where it was read: shown to the reader with the licence
    bytes        integer          NOT NULL DEFAULT 0,
    cut          boolean          NOT NULL DEFAULT false,        -- the text was cut at the limit of one chapter
    reads        integer          NOT NULL DEFAULT 0,            -- reads that were answered from here (the hot layer answers most of them)
    fetched_at   double precision NOT NULL,                      -- epoch seconds when the original page was read
    last_read_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (book_id, ch, parser)
);

CREATE INDEX IF NOT EXISTS book_chapters_last_read ON library.book_chapters (last_read_at);
