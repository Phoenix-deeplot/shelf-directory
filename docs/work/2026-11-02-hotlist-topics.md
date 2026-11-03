# Hotlist categories and event tracking (2026-11-02)

Status: implementation complete; awaiting user preview; not merged or deployed

## User checklist

- Reorganize hot-list categories as: Top stories, Tech, Latest AI news, Finance, World affairs, Entertainment, Learning & professional growth, Video & communities; Shopping remains optional.
- Promote platform charts, simplify mobile/desktop navigation, add Today / 7-day period semantics backed by real snapshots.
- Track evolving events across sources, merge related reports, show timestamps and source links, archive stale topics.
- Reorder sources using freshness, health, in-app readability and measured engagement; never overwrite the source platform's own chart rank.
- Preserve existing ops content and environment configuration; no deployment or production migration by Codex.

## Assignment

- Codex UI branch: `codex/hotlist-topics`, worktree `E:\Ztree\wt\codex-hotlist-topics` in the my-archive repo.
- Codex helper API branch: `codex/hotlist-topics-api`, worktree `E:\Ztree\wt\codex-hotlist-topics-api` in this repo.
- The API helper may only change hot-list service files, its additive migration, and relevant tests. Do not edit my-archive.

## API contract

- Preserve `GET /api/hot` fields `columns` and `platforms`; use stable category IDs `news`, `tech`, `ai`, `money`, `world`, `entertainment`, `learning`, `media`, `shopping`. Shopping is off by default.
- Preserve `GET /api/hot?p=<id>&format=json`; add optional `period=day|week`, defaulting to `day`. Week is computed from persisted snapshots, never fabricated from a current-only upstream response.
- Add `GET /api/hot/events?lang=zh&limit=...` returning `{ok, updated, events}`; each event includes `id`, `title`, `category`, `last_updated`, `source_count`, `trend`, and linked short-form items. Store only metadata and short feed descriptions, not full articles or copied images.
- Add an authenticated refresh route for scheduled invocation. Existing API behavior remains compatible.
- Add an idempotent SQL migration for chart snapshots and event/source records. Do not drop, rebuild, or rewrite existing `ops` tables.

## Source and update policy

- Start from reviewed existing sources: GDELT DOC for cross-source event discovery; WallStreetCN for Chinese finance; official AI news feeds (OpenAI News etc.) for AI announcements; arXiv remains under learning/research, not AI news.
- BBC, DW, RFI and other RSS sources require per-source display/redistribution review. Bloomberg is not assumed licensed or stable; WSJ and real-time gold quotes are not treated as free sources.
- Public event reads may refresh on demand with a 10-minute throttle and PostgreSQL advisory lock; keep the authenticated refresh route for scheduled invocation. Bound each upstream request to 2.5 seconds, using a backward-compatible optional timeout in `aggregate.py`; store one daily chart snapshot for a true rolling 7-day chart on Vercel Hobby. The UI must show actual update times. Higher-frequency unattended refresh requires a separate scheduler or plan capability.
- Event candidates are discovered from multiple reports, but surfaced claims link back to sources and are not presented as verified facts. Use source count and freshness as signals, plus manual pin/ignore metadata.

## Files and tests

- Code: `hotapi.py`, `api/hot.py`, new event/refresh API handlers, `aggregate.py` (optional bounded timeout parameter; default unchanged), a new additive `migrations/007_hotlist_events.sql`, and hot API tests.
- Keep the migration repeatable and test against an isolated test database; do not run against production.
- Run hot API unit tests, migration checks, API contract tests, and `git diff --check`; provide local preview only. No push, merge, or deployment.

## Risks

- Existing service returns current rankings only and has no persistent history.
- Vercel Hobby scheduled jobs run at most daily, so no 15-minute unattended guarantee.
- Event clustering may merge unrelated finance/conflict stories; preserve source evidence and allow manual ignore/pin.
- RSS availability is not a redistribution license; keep short excerpts and original links, and check each source's terms.

## Implementation and verification

- API implementation is committed on `codex/hotlist-topics-api` as `a1eb6e4` (`Add hot chart history and event tracking APIs`).
- Added compatible category migration, daily chart snapshots and 7-day aggregation, event tracking/read/refresh endpoints, and additive migration `migrations/007_hotlist_events.sql`.
- Public event reads attempt a refresh at most once per 10 minutes under a PostgreSQL advisory lock; stale stored events remain available if refresh fails or another refresh is in progress.
- The optional 2.5-second source timeout helper in `aggregate.py` is retained with explicit user approval; existing callers keep their prior timeout behavior by default.
- Verification reported by the implementation branch: `tests.test_hot` + `tests.test_hot_events` (52 tests) and `tests.test_aggregate` (97 tests) passed; syntax checks and `git diff --check` passed.
- The migration has not been run against any database. Apply it only after merging and reviewing the migration target; no production database was touched.
- The Zhihu Daily input was switched from Zhihu's latest-news JSON endpoint to the user-requested AnyFeeder RSS endpoint; the output remains a readable, non-ranked feed item.
