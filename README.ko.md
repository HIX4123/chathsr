# chathsr

`chathsr`는 ArcaLive 붕괴 스타레일 채널의 `정보` 카테고리 글을 대상으로 동작하는 Python CLI 기반 RAG 프로토타입입니다.
이제 수집 경로는 `cloudscraper` 기반 HTTP crawler 하나만 지원합니다. 브라우저 세션, 쿠키 import, CDP, WebSocket probe 기반 워크플로는 더 이상 지원 범위가 아닙니다.

## 요구 사항

- `curl`, `tar`, `python3`가 있는 Linux x86_64 환경
- Gemini API 키

`cloudscraper` 동작은 운영체제와 네트워크 환경에 따라 달라질 수 있습니다. 같은 명령이 Windows에서는 되고 Linux에서는 막힌다면, 이제는 그 차이를 브라우저/세션 경로가 아니라 HTTP crawler 환경 차이로 봅니다.

## 설치

```bash
./scripts/setup-python.sh
source .venv/bin/activate
```

`./scripts/setup-python.sh`는 프로젝트 로컬 `.python/` 아래에 standalone Python을 설치하고, `.venv/`를 다시 만든 뒤 `.[dev]`를 editable 모드로 설치합니다. 기본값은 Python `3.12.12`이며, 구버전 대체 경로가 필요하면 `./scripts/setup-python.sh 3.12.10`을 사용하면 됩니다.

이 설정은 의도적으로 `3.12.13`을 대상으로 하지 않습니다. 해당 버전은 이 환경에서 프로젝트 로컬 사전빌드 설치가 아니라 소스 빌드 경로로 돌아갈 가능성이 크기 때문입니다.

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 설정합니다.

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

## 권장 워크플로

1. 먼저 1페이지 스모크 테스트부터 실행합니다.

```bash
rag crawl export-jsonl ./exports/posts.jsonl --max-pages 1
rag crawl export-jsonl ./exports/posts.jsonl --max-pages 1 --verbose
```

2. 크롤링이 실제로 성공한 뒤에만 RAG 단계로 넘어갑니다.

```bash
rag import-posts ./exports/posts.jsonl
rag index changed-only
rag ask "방금 수집된 글 제목 내용을 요약해 줘"
```

3. Linux에서 계속 막히는데 Windows에서는 성공한다면, 같은 HTTP crawler 경로 안에서 환경 차이를 조사합니다.

```bash
rag probe http --verbose
rag probe http --proxy http://user:pass@proxy.example:8080 --verbose
rag probe http --cookie-header "cf_clearance=..." --profile default
rag probe http --output ./data/http-probe.json
```

4. Windows에서는 수집이 되는데 이 서버에서는 막힌다면, Windows에서 배치를 내보내고 이 서버로 동기화합니다.

```bash
rag crawl export-sync-batch --auto-since-server
rag sync push-latest
# 서버에서
rag sync inbox
```

## 명령어

로컬에서 자동 수집한 게시글 JSONL 파일을 가져옵니다.

```bash
rag import-posts /path/to/posts.jsonl
```

`정보` 카테고리 전체를 처음부터 수집합니다. 실제 운영 전에는 1페이지 스모크 테스트를 먼저 권장합니다.

```bash
rag crawl backfill
rag crawl backfill --max-pages 1 --verbose
```

SQLite에 바로 넣지 않고 JSONL로 내보냅니다.

```bash
rag crawl export-jsonl ./exports/posts.jsonl
rag crawl export-jsonl ./exports/posts.jsonl --verbose
```

서버 업로드용 sync batch 쌍을 만듭니다.

```bash
rag crawl export-sync-batch
rag crawl export-sync-batch --since-post-id 12345678
rag crawl export-sync-batch --auto-since-server
rag crawl export-sync-batch --auto-since-server --recheck-posts 50
rag crawl export-sync-batch ./exports --max-pages 1 --verbose
```

새로 올라온 글이나 수정된 글을 동기화합니다.

```bash
rag sync
rag sync --verbose
```

가장 최신 로컬 sync batch를 원격 inbox로 업로드합니다.

```bash
rag sync push-latest
rag sync push-latest --batch-id 20260331T120102Z-a1b2c3 --verbose
```

서버 inbox에 올라온 sync batch를 import하고 인덱싱합니다.

```bash
rag sync inbox
rag sync inbox --verbose
```

클라이언트 incremental export가 참조하는 서버 기준 동기화 지점을 출력합니다.

```bash
rag sync status
rag sync status --json
rag sync status --json --recent-posts 20
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
rag refresh --verbose
```

내장 요청 프로필 매트릭스로 HTTP crawler를 진단합니다.

```bash
rag probe http
rag probe http --url https://arca.live/b/hkstarrail --verbose
rag probe http --proxy http://user:pass@proxy.example:8080 --profile default
rag probe http --cookie-header "cf_clearance=..." --profile default
rag probe http --cookie-json ./cookies.json --output ./data/http-probe.json
rag probe http --output ./data/http-probe.json
```

## 데이터

- SQLite 데이터베이스: `data/chathsr.sqlite3`
- 서버 sync inbox: `data/inbox`
- 처리 완료/실패 sync archive: `data/sync-archive`
- 로컬 sync batch outbox: `data/sync-outbox`
- 선택적 HTTP probe 로그: `rag probe http --output`에 넘긴 경로
- 로컬 수집 JSONL: `rag crawl export-jsonl`에 넘긴 경로
- MVP에서는 게시글 본문 텍스트만 인덱싱합니다.
- 이미지는 OCR하지 않고 URL 메타데이터만 저장합니다.

## cron 예시

로컬 머신에서 매일 03:00에 갱신하는 예시입니다.

```cron
0 3 * * * cd /path/to/chathsr && . .venv/bin/activate && rag refresh >> data/refresh.log 2>&1
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
- 대부분의 명령은 `--verbose`를 받아 stderr에 자세한 진행 로그를 출력합니다.
- crawl, sync, refresh 명령은 실제 요청 URL도 함께 출력하고, `refresh --verbose`는 인덱싱 로그까지 포함합니다.
- 지원하는 수집 경로는 HTTP crawler 하나뿐입니다.
- `rag probe http`는 DB를 건드리지 않고 cloudscraper 실험 매트릭스를 실행하는 진단용 명령입니다.
- probe 전용으로 `--proxy`, `--cookie-header`, `--cookie-json`, `--profile`을 써서 직결, 프록시, 쿠키 조합을 비교할 수 있습니다.
- `rag sync push-latest`는 로컬 `ssh`, `scp` 실행 파일을 호출하므로 클라이언트 머신 PATH에 OpenSSH가 있어야 합니다.
- `rag crawl export-sync-batch --auto-since-server`는 새 글뿐 아니라 최근 재검사 윈도우를 함께 export해서 수정된 글도 감지합니다.
- `--auto-since-server`를 쓰면 기본 recent recheck window는 최신 글 `20`개이고, 그 외 경로에서는 기본값이 `0`입니다.
- recent recheck window 밖의 아주 오래된 수정은 여전히 놓칠 수 있으므로, 필요하면 `--recheck-posts` 값을 키우거나 더 넓은 범위의 주기적 sync를 함께 운영해야 합니다.
