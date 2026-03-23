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

이 상태 파일을 쓰는 경우 `PLAYWRIGHT_STORAGE_STATE_PATH`가 persistent 프로필보다 우선합니다.

로컬에서 Playwright를 설치할 수 없는 환경이라면, 브라우저 확장으로 쿠키를 JSON으로 내보낸 뒤 같은 명령으로 가져오면 됩니다.

`정보` 카테고리 전체를 처음부터 수집합니다.

```bash
rag crawl backfill
```

새로 올라온 글이나 수정된 글을 동기화합니다.

```bash
rag sync
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

## 데이터

- SQLite 데이터베이스: `data/chathsr.sqlite3`
- Playwright 프로필: `data/playwright-profile/`
- 가져온 세션 상태: `data/storage_state.json`
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
