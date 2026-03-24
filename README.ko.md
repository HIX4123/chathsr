# chathsr

`chathsr`는 ArcaLive 붕괴 스타레일 채널의 `정보` 카테고리 글을 대상으로 동작하는 Python CLI 기반 RAG 프로토타입입니다.

## 요구 사항

- Python 3.12
- Gemini API 키
- ArcaLive의 Cloudflare 및 로그인 절차를 통과할 수 있는 브라우저 세션

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
```

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 설정합니다.

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

## 명령어

Cloudflare 통과 및 로그인용 persistent 브라우저 프로필을 저장합니다.

```bash
rag auth
```

다른 머신에서 저장한 Playwright `storage_state.json` 또는 브라우저 확장 쿠키 JSON을 가져옵니다.

```bash
rag import-state /path/to/storage_state.json
```

로컬에서 자동 수집한 게시글 JSONL 파일을 가져옵니다.

```bash
rag import-posts /path/to/posts.jsonl
```

이 상태 파일을 쓰는 경우 `PLAYWRIGHT_STORAGE_STATE_PATH`가 persistent 프로필보다 우선합니다.

로컬에서 Playwright를 설치할 수 없는 환경이라면, 브라우저 확장으로 쿠키를 JSON으로 내보낸 뒤 같은 명령으로 가져오면 됩니다.

`정보` 카테고리 전체를 처음부터 수집합니다.

```bash
rag crawl backfill
rag crawl backfill --transport custom-http
```

SQLite에 바로 넣지 않고 JSONL로 내보냅니다.

```bash
rag crawl export-jsonl ./exports/posts.jsonl
rag crawl export-jsonl ./exports/posts.jsonl --transport custom-http
```

새로 올라온 글이나 수정된 글을 동기화합니다.

```bash
rag sync
rag sync --transport custom-http
```

새 글 또는 변경된 글만 임베딩합니다.

```bash
rag index changed-only
```

임베딩 모델을 바꾼 뒤 전체 벡터 저장소를 다시 생성합니다.

```bash
rag index full-reembed
```

로컬 RAG 저장소를 대상으로 질문합니다.

```bash
rag ask "로프 캐릭터 육성 우선순위가 뭐야?"
```

동기화 후 변경분만 다시 인덱싱합니다.

```bash
rag refresh
rag refresh --transport custom-http
```

로컬 remote-debugging 브라우저의 websocket 트래픽을 기록하고 요약합니다.

```bash
rag probe websocket --cdp-url http://127.0.0.1:9222 --duration 60 --output ./data/ws-probe.jsonl
rag probe summarize ./data/ws-probe.jsonl
```

로그인 또는 디버깅 중 브라우저를 눈으로 확인해야 하면 `--headful`을 사용합니다.

```bash
rag auth
rag sync --headful
```

GUI가 있는 로컬 머신에서 세션을 내보내려면, 수동 로그인 후 Playwright storage state를 저장합니다.

```python
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://arca.live/b/hkstarrail")
    input("Cloudflare/로그인을 완료한 뒤 Enter를 누르세요...")
    context.storage_state(path=str(Path("storage_state.json")))
    browser.close()
```

로컬 브라우저 환경에서 Playwright를 직접 쓸 수 없다면, 브라우저 확장으로 쿠키를 JSON으로 내보내서 `rag import-state`에 넣으세요.

나중에 네가 직접 HTTP 기반 수집 코드를 붙일 계획이라면, `src/chathsr/custom_transport.py`의 placeholder를 구현한 뒤 `--transport custom-http`로 crawl/sync/refresh 명령을 실행하면 됩니다.

세션 파일을 다른 머신으로 옮겨도 계속 막힌다면, 세션 자체를 옮기지 말고 로컬 브라우저에 직접 붙는 흐름으로 전환하면 됩니다.

1. Chrome 또는 Edge를 별도 remote-debugging 프로필로 실행합니다.
2. 그 브라우저에서 Cloudflare와 로그인을 직접 완료합니다.
3. `PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222`를 설정합니다.
4. 로컬 머신에서 `rag crawl export-jsonl ./exports/posts.jsonl`을 실행합니다.
5. 생성된 JSONL을 업로드한 뒤 여기서 `rag import-posts ./exports/posts.jsonl`을 실행합니다.

웹소켓이 단순 새글 이벤트만 주는지, 더 풍부한 게시글 데이터를 주는지 확인하고 싶다면 같은 remote-debugging 브라우저를 대상으로 먼저 websocket probe를 돌리면 됩니다.

## 데이터

- SQLite 데이터베이스: `data/chathsr.sqlite3`
- Playwright 프로필: `data/playwright-profile/`
- 가져온 세션 상태: `data/storage_state.json`
- 로컬 수집 JSONL: `rag crawl export-jsonl`에 넘긴 경로
- websocket probe 로그: `rag probe websocket --output`에 넘긴 경로
- MVP에서는 게시글 본문 텍스트만 인덱싱합니다.
- 이미지는 OCR하지 않고 URL 메타데이터만 저장합니다.

## cron 예시

매일 03:00에 갱신하는 예시입니다.

```cron
0 3 * * * cd /workspaces/chathsr && . .venv/bin/activate && rag refresh >> data/refresh.log 2>&1
```

## 테스트

```bash
pytest
python -m compileall src
```

## 참고

- 기본 생성 모델은 `gemini-3-flash-preview`입니다.
- 저비용 fallback 생성 모델은 `gemini-3.1-flash-lite-preview`입니다.
- 기본 임베딩 모델은 `gemini-embedding-2-preview`입니다.
- `PLAYWRIGHT_STORAGE_STATE_PATH`는 가져온 `storage_state.json`을 가리키며, 이 값이 있으면 persistent 프로필보다 우선합니다.
- `PLAYWRIGHT_CDP_URL`를 설정하면, 크롤러가 자체 브라우저를 띄우는 대신 이미 열려 있는 로컬 Chrome/Edge 세션에 붙습니다.
- `--transport browser|custom-http`로 수집 transport를 명시적으로 선택할 수 있고, 기본값은 계속 `browser`입니다.
- `rag probe websocket`은 로컬 브라우저의 CDP websocket 이벤트를 기록하는 진단용 명령이며, 게시글 수집을 직접 수행하지는 않습니다.
