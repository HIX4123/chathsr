from __future__ import annotations

import json
from contextlib import contextmanager

from typer.testing import CliRunner

from chathsr.cli import app
from chathsr.http_transport import HTTPProbeResult
from tests.helpers import make_article


runner = CliRunner()


def test_crawl_backfill_help_omits_transport_and_headless() -> None:
    result = runner.invoke(app, ["crawl", "backfill", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.stdout
    assert "--headless" not in result.stdout
    assert "--verbose" in result.stdout


def test_crawl_export_jsonl_help_omits_transport_and_headless() -> None:
    result = runner.invoke(app, ["crawl", "export-jsonl", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.stdout
    assert "--headless" not in result.stdout
    assert "--verbose" in result.stdout


def test_crawl_export_sync_batch_help_omits_transport_and_headless() -> None:
    result = runner.invoke(app, ["crawl", "export-sync-batch", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.stdout
    assert "--headless" not in result.stdout
    assert "--since-post-id" in result.stdout
    assert "--auto-since-server" in result.stdout
    assert "--recheck-posts" in result.stdout
    assert "--verbose" in result.stdout


def test_sync_help_omits_transport_and_headless() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.stdout
    assert "--headless" not in result.stdout
    assert "--verbose" in result.stdout
    assert "push-latest" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout


def test_refresh_help_omits_transport_and_headless() -> None:
    result = runner.invoke(app, ["refresh", "--help"])
    assert result.exit_code == 0
    assert "--transport" not in result.stdout
    assert "--headless" not in result.stdout
    assert "--verbose" in result.stdout


def test_probe_group_is_listed_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "probe" in result.stdout
    assert "auth" not in result.stdout
    assert "import-state" not in result.stdout


def test_probe_help_lists_http_subcommand() -> None:
    result = runner.invoke(app, ["probe", "--help"])
    assert result.exit_code == 0
    assert "http" in result.stdout


def test_probe_http_help_includes_options() -> None:
    result = runner.invoke(app, ["probe", "http", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.stdout
    assert "--output" in result.stdout
    assert "--proxy" in result.stdout
    assert "--cookie-header" in result.stdout
    assert "--cookie-json" in result.stdout
    assert "--profile" in result.stdout
    assert "--verbose" in result.stdout


def test_probe_http_uses_board_url_and_writes_output(monkeypatch, settings, tmp_path) -> None:
    output = tmp_path / "probe.json"

    monkeypatch.setattr("chathsr.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "chathsr.cli.run_http_probe_matrix",
        lambda _settings, url, *, verbose=False, proxy_url=None, cookie_header=None, cookie_jar=None, profile_name=None: [
            HTTPProbeResult(
                profile="default",
                url=url,
                final_url=url,
                status_code=403,
                blocked=True,
                block_marker_found=True,
                server="cloudflare",
                cf_ray="abc123",
                content_type="text/html",
                user_agent="ua",
                response_bytes=120,
                redirect_count=1,
                proxy_label="direct",
                cookie_mode="none",
                error_kind="challenge_page",
                error_detail="blocked",
                body_snippet="Just a moment...",
            )
        ],
    )

    result = runner.invoke(app, ["probe", "http", "--output", str(output)])

    assert result.exit_code == 0
    assert f"HTTP probe target={settings.board_url}" in result.stdout
    assert "default [direct/none]: status=403 blocked=yes redirects=1 error=challenge_page" in result.stdout
    assert "No working combination found. Result summary: challenge_page=1" in result.stdout
    assert "Saved probe output to" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["profile"] == "default"
    assert payload[0]["url"] == settings.board_url
    assert payload[0]["proxy_label"] == "direct"


def test_probe_http_forwards_proxy_cookie_and_profile_inputs(
    monkeypatch, settings, tmp_path
) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(json.dumps([{"name": "cf_clearance", "value": "token"}]), encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr("chathsr.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "chathsr.cli.run_http_probe_matrix",
        lambda _settings, url, *, verbose=False, proxy_url=None, cookie_header=None, cookie_jar=None, profile_name=None: captured.update(
            {
                "url": url,
                "verbose": verbose,
                "proxy_url": proxy_url,
                "cookie_header": cookie_header,
                "cookie_jar": cookie_jar,
                "profile_name": profile_name,
            }
        )
        or [],
    )

    result = runner.invoke(
        app,
        [
            "probe",
            "http",
            "--proxy",
            "http://user:pass@proxy.example:8080",
            "--cookie-json",
            str(cookie_path),
            "--profile",
            "default",
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == settings.board_url
    assert captured["verbose"] is True
    assert captured["proxy_url"] == "http://user:pass@proxy.example:8080"
    assert captured["cookie_header"] is None
    assert captured["cookie_jar"].get("cf_clearance") == "token"
    assert captured["profile_name"] == "default"


def test_probe_http_uses_proxy_env_by_default(monkeypatch, settings) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("chathsr.cli.load_settings", lambda: settings)
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.example:9000")
    monkeypatch.setattr(
        "chathsr.cli.run_http_probe_matrix",
        lambda _settings, url, *, verbose=False, proxy_url=None, cookie_header=None, cookie_jar=None, profile_name=None: captured.update(
            {"proxy_url": proxy_url}
        )
        or [],
    )

    result = runner.invoke(app, ["probe", "http"])

    assert result.exit_code == 0
    assert captured["proxy_url"] == "http://env-proxy.example:9000"


def test_probe_http_rejects_multiple_cookie_inputs(monkeypatch, settings, tmp_path) -> None:
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr("chathsr.cli.load_settings", lambda: settings)

    result = runner.invoke(
        app,
        [
            "probe",
            "http",
            "--cookie-header",
            "foo=bar",
            "--cookie-json",
            str(cookie_path),
        ],
    )

    assert result.exit_code != 0
    assert "Choose either --cookie-header or --cookie-json" in result.output


def test_top_level_commands_help_include_verbose() -> None:
    for command in (
        ["import-posts", "--help"],
        ["index", "changed-only", "--help"],
        ["index", "full-reembed", "--help"],
        ["ask", "--help"],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0
        assert "--verbose" in result.stdout


def test_refresh_verbose_is_forwarded_to_indexing(monkeypatch, settings) -> None:
    calls: dict[str, bool | None] = {"sync": None, "index": None}

    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        yield settings, object()

    class FakeCrawler:
        def __init__(self, _settings) -> None:
            pass

        def sync(
            self,
            _db,
            *,
            max_pages=None,
            verbose=False,
            unchanged_limit=20,
        ):
            calls["sync"] = verbose
            return {"pages": 1, "articles": 2, "new_posts": 1, "changed_posts": 1}

    class FakeGeminiClient:
        def __init__(self, _settings) -> None:
            pass

    def fake_index_posts(
        _db,
        _settings,
        _gemini,
        *,
        changed_only,
        full_reembed,
        verbose=False,
    ) -> int:
        calls["index"] = verbose
        assert changed_only is True
        assert full_reembed is False
        return 3

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)
    monkeypatch.setattr("chathsr.cli.ArcaLiveCrawler", FakeCrawler)
    monkeypatch.setattr("chathsr.cli.GeminiClient", FakeGeminiClient)
    monkeypatch.setattr("chathsr.cli.index_posts", fake_index_posts)

    result = runner.invoke(app, ["refresh", "--verbose"])

    assert result.exit_code == 0
    assert calls == {"sync": True, "index": True}
    assert "indexed=3" in result.stdout


def test_sync_push_latest_invokes_upload(monkeypatch, settings) -> None:
    calls: dict[str, object] = {}

    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        class FakeDb:
            def set_crawl_state(self, key: str, value: str) -> None:
                calls["crawl_state"] = (key, value)

        yield settings, FakeDb()

    class FakeBatch:
        batch_id = "20260331T120102Z-a1b2c3"

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)
    monkeypatch.setattr("chathsr.cli.find_sync_batch", lambda _path, *, batch_id=None: FakeBatch())
    monkeypatch.setattr("chathsr.cli.load_sync_batch_metadata", lambda _batch: object())
    monkeypatch.setattr(
        "chathsr.cli.push_sync_batch",
        lambda batch, current_settings, *, verbose=False: calls.update(
            {"batch_id": batch.batch_id, "settings": current_settings, "verbose": verbose}
        ),
    )

    result = runner.invoke(app, ["sync", "push-latest", "--verbose"])

    assert result.exit_code == 0
    assert calls["batch_id"] == FakeBatch.batch_id
    assert calls["settings"] is settings
    assert calls["verbose"] is True
    assert calls["crawl_state"] == ("last_pushed_sync_batch_id", FakeBatch.batch_id)


def test_crawl_export_sync_batch_uses_remote_sync_status(monkeypatch, settings) -> None:
    calls: dict[str, object] = {}

    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        class FakeDb:
            def set_crawl_state(self, key: str, value: str) -> None:
                calls["crawl_state"] = (key, value)

        yield settings, FakeDb()

    class FakeCrawler:
        def __init__(self, _settings) -> None:
            pass

        def crawl_incremental_articles(
            self,
            *,
            since_post_id=None,
            recheck_posts=0,
            max_pages=None,
            verbose=False,
        ):
            calls["crawl"] = {
                "since_post_id": since_post_id,
                "recheck_posts": recheck_posts,
                "max_pages": max_pages,
                "verbose": verbose,
            }
            return [
                make_article(post_id=12345678, title="새 글", body_text="새 글 본문"),
                make_article(post_id=12345555, title="최근 수정", body_text="최근 수정 본문"),
                make_article(post_id=12345554, title="변경 없음", body_text="변경 없음"),
            ], {"pages": 1, "articles": 3, "new_posts": 0, "changed_posts": 0}

    class FakeStatus:
        latest_post_id = 12345600
        recent_posts = [
            type("RecentPost", (), {"post_id": 12345555, "content_hash": "old-hash"}),
            type("RecentPost", (), {"post_id": 12345554, "content_hash": make_article(post_id=12345554, title="변경 없음", body_text="변경 없음").content_hash}),
        ]

    class FakeBatch:
        batch_id = "20260402T000000Z-a1b2c3"
        jsonl_path = "batch.jsonl"
        metadata_path = "batch.metadata.json"

    class FakeMetadata:
        article_count = 2
        since_post_id = 12345600
        min_post_id = 12345555
        max_post_id = 12345678
        recheck_posts = 20

    class FakeResult:
        batch = FakeBatch()
        metadata = FakeMetadata()

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)
    monkeypatch.setattr("chathsr.cli.ArcaLiveCrawler", FakeCrawler)
    monkeypatch.setattr(
        "chathsr.cli.read_remote_sync_status",
        lambda _settings, *, recent_posts=0, verbose=False: calls.update(
            {"recent_posts": recent_posts}
        )
        or FakeStatus(),
    )
    monkeypatch.setattr(
        "chathsr.cli.create_sync_batch",
        lambda output_dir, articles, *, settings, since_post_id, recheck_posts, max_pages: calls.update(
            {
                "create": {
                    "output_dir": output_dir,
                    "articles": articles,
                    "since_post_id": since_post_id,
                    "recheck_posts": recheck_posts,
                    "max_pages": max_pages,
                }
            }
        )
        or FakeResult(),
    )

    result = runner.invoke(app, ["crawl", "export-sync-batch", "--auto-since-server", "--verbose"])

    assert result.exit_code == 0
    assert calls["recent_posts"] == 20
    assert calls["crawl"] == {
        "since_post_id": 12345600,
        "recheck_posts": 20,
        "max_pages": None,
        "verbose": True,
    }
    assert calls["create"]["since_post_id"] == 12345600
    assert calls["create"]["recheck_posts"] == 20
    assert [article.post_id for article in calls["create"]["articles"]] == [12345678, 12345555]
    assert calls["crawl_state"] == ("last_export_sync_batch_id", FakeBatch.batch_id)


def test_crawl_export_sync_batch_rejects_multiple_since_inputs() -> None:
    result = runner.invoke(
        app,
        ["crawl", "export-sync-batch", "--since-post-id", "123", "--auto-since-server"],
    )

    assert result.exit_code != 0
    assert "Choose either --since-post-id or --auto-since-server" in result.output


def test_crawl_export_sync_batch_skips_empty_recheck_batch(monkeypatch, settings) -> None:
    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        class FakeDb:
            def set_crawl_state(self, key: str, value: str) -> None:
                raise AssertionError("crawl state should not be updated when no batch is exported")

        yield settings, FakeDb()

    class FakeCrawler:
        def __init__(self, _settings) -> None:
            pass

        def crawl_incremental_articles(
            self,
            *,
            since_post_id=None,
            recheck_posts=0,
            max_pages=None,
            verbose=False,
        ):
            return [
                make_article(post_id=12345554, title="변경 없음", body_text="변경 없음"),
            ], {"pages": 1, "articles": 1, "new_posts": 0, "changed_posts": 0}

    class FakeStatus:
        latest_post_id = 12345600
        recent_posts = [
            type(
                "RecentPost",
                (),
                {
                    "post_id": 12345554,
                    "content_hash": make_article(
                        post_id=12345554,
                        title="변경 없음",
                        body_text="변경 없음",
                    ).content_hash,
                },
            ),
        ]

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)
    monkeypatch.setattr("chathsr.cli.ArcaLiveCrawler", FakeCrawler)
    monkeypatch.setattr(
        "chathsr.cli.read_remote_sync_status",
        lambda _settings, *, recent_posts=0, verbose=False: FakeStatus(),
    )

    result = runner.invoke(app, ["crawl", "export-sync-batch", "--auto-since-server"])

    assert result.exit_code == 0
    assert "No new or changed posts to export" in result.stdout


def test_sync_status_json_output(monkeypatch, settings) -> None:
    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        class FakeDb:
            def get_latest_post_summary(self):
                return {"post_id": 12345678, "crawled_at": "2026-04-02T00:00:00Z"}

            def get_latest_successful_sync_batch_id(self):
                return "batch-1"

            def list_recent_posts(self, *, limit: int):
                assert limit == 2
                return [
                    {"post_id": 12345678, "content_hash": "hash-1"},
                    {"post_id": 12345555, "content_hash": "hash-2"},
                ]

        yield settings, FakeDb()

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)

    result = runner.invoke(app, ["sync", "status", "--json", "--recent-posts", "2"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "latest_post_id": 12345678,
        "latest_crawled_at": "2026-04-02T00:00:00Z",
        "latest_batch_id": "batch-1",
        "recent_posts": [
            {"post_id": 12345678, "content_hash": "hash-1"},
            {"post_id": 12345555, "content_hash": "hash-2"},
        ],
    }


def test_sync_inbox_invokes_batch_processing(monkeypatch, settings) -> None:
    calls: dict[str, object] = {}

    @contextmanager
    def fake_command_context(command: str, detail: str = ""):
        yield settings, object()

    monkeypatch.setattr("chathsr.cli.command_context", fake_command_context)
    monkeypatch.setattr(
        "chathsr.cli._sync_inbox_batches",
        lambda current_settings, _db, *, verbose=False: calls.update(
            {"settings": current_settings, "verbose": verbose}
        )
        or {
            "batches": 1,
            "processed_batches": 1,
            "skipped_batches": 0,
            "failed_batches": 0,
            "articles": 2,
            "new_posts": 1,
            "changed_posts": 1,
            "indexed": 1,
        },
    )

    result = runner.invoke(app, ["sync", "inbox", "--verbose"])

    assert result.exit_code == 0
    assert calls["settings"] is settings
    assert calls["verbose"] is True
    assert "processed=1" in result.stdout
