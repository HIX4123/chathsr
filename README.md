# chathsr

`chathsr` is a Python CLI RAG prototype for ArcaLive's Honkai Star Rail `정보` posts.
The crawler now uses a single `cloudscraper`-backed HTTP path. Browser/session/CDP workflows are no longer part of the supported runtime.

## Requirements

- Linux x86_64 with `curl`, `tar`, and `python3`
- A Gemini API key

`cloudscraper` behavior can still differ by OS and network environment. If the same crawl succeeds on Windows and fails on Linux, treat that as an environment-level difference in the HTTP path rather than a browser/session issue.

## Setup

```bash
./scripts/setup-python.sh
source .venv/bin/activate
```

`./scripts/setup-python.sh` installs a project-local standalone Python into `.python/`, recreates `.venv/`, and installs `.[dev]` in editable mode. The default is Python `3.12.12`; use `./scripts/setup-python.sh 3.12.10` if you need the older fallback.

This setup intentionally does not target `3.12.13`, because the Linux path there would fall back to a source-build workflow instead of a project-local prebuilt install.

Create a `.env` file at the project root:

```env
GEMINI_API_KEY=your-key
DATA_DIR=./data
DATABASE_PATH=./data/chathsr.sqlite3
SYNC_INBOX_DIR=./data/inbox
SYNC_ARCHIVE_DIR=./data/sync-archive
SYNC_CLIENT_OUTBOX_DIR=./data/sync-outbox
SYNC_REMOTE_HOST=server.example.com
SYNC_REMOTE_USER=ubuntu
SYNC_REMOTE_PATH=/srv/chathsr/inbox
SYNC_SSH_PORT=22
GENERATION_MODEL=gemini-3-flash-preview
CHEAP_GENERATION_MODEL=gemini-3.1-flash-lite-preview
EMBEDDING_MODEL=gemini-embedding-2-preview
EMBEDDING_DIM=1536
BOARD_SLUG=hkstarrail
CATEGORY_LABEL=정보
TOP_K=6
```

## Recommended Workflow

1. Run a one-page smoke test first.

```bash
rag crawl export-jsonl ./exports/posts.jsonl --max-pages 1
rag crawl export-jsonl ./exports/posts.jsonl --max-pages 1 --verbose
```

2. Only after crawl succeeds, continue into RAG.

```bash
rag import-posts ./exports/posts.jsonl
rag index changed-only
rag ask "방금 수집된 글 제목 내용을 요약해 줘"
```

3. If Linux keeps getting blocked while Windows succeeds, keep investigating within the same HTTP crawler path.

```bash
rag probe http --verbose
rag probe http --proxy http://user:pass@proxy.example:8080 --verbose
rag probe http --cookie-header "cf_clearance=..." --profile default
rag probe http --output ./data/http-probe.json
```

4. If Windows can crawl reliably and this server cannot, export from Windows and sync the batch here.

```bash
rag crawl export-sync-batch --auto-since-server
rag sync push-latest
# on the server
rag sync inbox
```

## Commands

Import locally exported post JSONL files:

```bash
rag import-posts /path/to/posts.jsonl
```

Backfill the full `정보` category after a smoke test succeeds:

```bash
rag crawl backfill
rag crawl backfill --max-pages 1 --verbose
```

Export crawled posts as JSONL instead of writing them straight into SQLite:

```bash
rag crawl export-jsonl ./exports/posts.jsonl
rag crawl export-jsonl ./exports/posts.jsonl --verbose
```

Export crawled posts as a sync batch pair for later upload:

```bash
rag crawl export-sync-batch
rag crawl export-sync-batch --since-post-id 12345678
rag crawl export-sync-batch --auto-since-server
rag crawl export-sync-batch --auto-since-server --recheck-posts 50
rag crawl export-sync-batch ./exports --max-pages 1 --verbose
```

Sync newly added or edited posts:

```bash
rag sync
rag sync --verbose
```

Upload the newest local sync batch to the configured remote inbox:

```bash
rag sync push-latest
rag sync push-latest --batch-id 20260331T120102Z-a1b2c3 --verbose
```

Import and index uploaded sync batches on the server:

```bash
rag sync inbox
rag sync inbox --verbose
```

Print the server-side sync cursor used by incremental client exports:

```bash
rag sync status
rag sync status --json
rag sync status --json --recent-posts 20
```

Embed only new or changed posts:

```bash
rag index changed-only
```

Rebuild the full vector store after changing the embedding model:

```bash
rag index full-reembed
```

Ask a question against the local RAG store:

```bash
rag ask "로프 캐릭터 육성 우선순위가 뭐야?"
```

Run sync plus incremental indexing:

```bash
rag refresh
rag refresh --verbose
```

Probe the HTTP crawler with the built-in request profile matrix:

```bash
rag probe http
rag probe http --url https://arca.live/b/hkstarrail --verbose
rag probe http --proxy http://user:pass@proxy.example:8080 --profile default
rag probe http --cookie-header "cf_clearance=..." --profile default
rag probe http --cookie-json ./cookies.json --output ./data/http-probe.json
rag probe http --output ./data/http-probe.json
```

## Data

- SQLite database: `data/chathsr.sqlite3`
- Server sync inbox: `data/inbox`
- Processed/failed sync archives: `data/sync-archive`
- Local sync batch outbox: `data/sync-outbox`
- Optional HTTP probe logs: any path you pass to `rag probe http --output`
- Local JSONL exports: any path you pass to `rag crawl export-jsonl`
- Only post body text is indexed in the MVP.
- Images are preserved as URLs in metadata, not OCR'd.

## Cron

Example daily refresh at 03:00 on the local machine:

```cron
0 3 * * * cd /path/to/chathsr && . .venv/bin/activate && rag refresh >> data/refresh.log 2>&1
```

## Testing

```bash
pytest
python -m compileall src
```

## Notes

- The default generation model is `gemini-3-flash-preview`.
- The fallback cheaper model is `gemini-3.1-flash-lite-preview`.
- The default embedding model is `gemini-embedding-2-preview`.
- Most commands accept `--verbose` and print detailed progress logs to stderr.
- Crawl, sync, and refresh commands additionally print requested URLs, and `refresh --verbose` includes indexing logs too.
- The supported crawl path is the HTTP crawler only.
- `rag probe http` runs a cloudscraper experiment matrix without touching crawl state.
- Probe-only inputs can compare direct egress, proxy egress, and optional cookie injection through `--proxy`, `--cookie-header`, `--cookie-json`, and `--profile`.
- `rag sync push-latest` currently shells out to local `ssh` and `scp`, so the client machine must have OpenSSH available on `PATH`.
- `rag crawl export-sync-batch --auto-since-server` now exports new posts plus a recent recheck window to detect edited posts.
- The default recheck window is `20` newest posts when `--auto-since-server` is used, or `0` otherwise.
- Very old edits outside the recheck window can still be missed; use a larger `--recheck-posts` value or a periodic broader sync when needed.
