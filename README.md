# chathsr

`chathsr` is a Python CLI RAG prototype for ArcaLive's Honkai Star Rail `정보` posts.

## Requirements

- Python 3.12
- A Gemini API key
- A browser session that can pass ArcaLive's Cloudflare/login flow

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
```

Create a `.env` file at the project root:

```env
GEMINI_API_KEY=your-key
DATA_DIR=./data
PLAYWRIGHT_PROFILE_DIR=./data/playwright-profile
PLAYWRIGHT_STORAGE_STATE_PATH=./data/storage_state.json
PLAYWRIGHT_CDP_URL=
DATABASE_PATH=./data/chathsr.sqlite3
GENERATION_MODEL=gemini-3-flash-preview
CHEAP_GENERATION_MODEL=gemini-3.1-flash-lite-preview
EMBEDDING_MODEL=gemini-embedding-2-preview
EMBEDDING_DIM=1536
BOARD_SLUG=hkstarrail
CATEGORY_LABEL=정보
TOP_K=6
```

## Commands

Authenticate and save the persistent browser profile:

```bash
rag auth
```

Import a Playwright `storage_state.json` or browser-extension cookie JSON exported on another machine:

```bash
rag import-state /path/to/storage_state.json
```

Import locally exported post JSONL files:

```bash
rag import-posts /path/to/posts.jsonl
```

If you are using an imported session state, the crawler will prefer `PLAYWRIGHT_STORAGE_STATE_PATH` over the persistent profile.

If Playwright cannot be installed locally, export cookies from a browser extension, then import that JSON with the same command.

Backfill the full `정보` category:

```bash
rag crawl backfill
rag crawl backfill --transport custom-http
```

Export crawled posts as JSONL instead of writing them straight into SQLite:

```bash
rag crawl export-jsonl ./exports/posts.jsonl
rag crawl export-jsonl ./exports/posts.jsonl --transport custom-http
```

Sync newly added or edited posts:

```bash
rag sync
rag sync --transport custom-http
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
rag refresh --transport custom-http
```

Probe websocket traffic from a local remote-debugging browser and summarize the result:

```bash
rag probe websocket --cdp-url http://127.0.0.1:9222 --duration 60 --output ./data/ws-probe.jsonl
rag probe summarize ./data/ws-probe.jsonl
```

Use `--headful` if you need a visible browser during login/debugging:

```bash
rag auth
rag sync --headful
```

If you need to export a session on a local machine with a GUI, save the Playwright storage state after manual login:

```python
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://arca.live/b/hkstarrail")
    input("Complete Cloudflare/login, then press Enter...")
    context.storage_state(path=str(Path("storage_state.json")))
    browser.close()
```

If your local browser environment cannot run Playwright, export cookies from a browser extension as JSON and import that file with `rag import-state`.

If you want to add your own HTTP-based collector later, implement the placeholder at `src/chathsr/custom_transport.py` and run crawl/sync/refresh commands with `--transport custom-http`.

If moving session state between machines still gets blocked, keep the authenticated browser on your local machine and attach to it over CDP instead of transferring cookies/state.

1. Start Chrome or Edge with a separate remote-debugging profile.
2. Complete Cloudflare/login manually in that browser.
3. Set `PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222`.
4. Run `rag crawl export-jsonl ./exports/posts.jsonl` on that local machine.
5. Upload the JSONL file here and run `rag import-posts ./exports/posts.jsonl`.

If you need to inspect whether websocket traffic only carries post IDs or exposes richer post data, run the websocket probe against that same remote-debugging browser before changing the collector design.

## Data

- SQLite database: `data/chathsr.sqlite3`
- Playwright profile: `data/playwright-profile/`
- Imported session state: `data/storage_state.json`
- Local JSONL exports: any path you pass to `rag crawl export-jsonl`
- Websocket probe logs: any path you pass to `rag probe websocket --output`
- Only post body text is indexed in the MVP.
- Images are preserved as URLs in metadata, not OCR'd.

## Cron

Example daily refresh at 03:00:

```cron
0 3 * * * cd /workspaces/chathsr && . .venv/bin/activate && rag refresh >> data/refresh.log 2>&1
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
- `PLAYWRIGHT_STORAGE_STATE_PATH` can point to an imported `storage_state.json` and override the persistent profile.
- `PLAYWRIGHT_CDP_URL` lets the crawler attach to an already-open local Chrome/Edge session instead of launching its own browser.
- `--transport browser|custom-http` selects the crawl transport explicitly, and the default remains `browser`.
- `rag probe websocket` is a local diagnostic command for CDP websocket event capture; it does not crawl posts by itself.
