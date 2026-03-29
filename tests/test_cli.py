from __future__ import annotations

import pytest

from typer.testing import CliRunner

from chathsr.cli import app


runner = CliRunner()


def test_crawl_backfill_help_includes_transport_option() -> None:
    result = runner.invoke(app, ["crawl", "backfill", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "--verbose" in result.stdout
    assert "custom-http" in result.stdout


def test_crawl_export_jsonl_help_includes_transport_option() -> None:
    result = runner.invoke(app, ["crawl", "export-jsonl", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "--verbose" in result.stdout
    assert "custom-http" in result.stdout


def test_sync_help_includes_transport_option() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "--verbose" in result.stdout
    assert "custom-http" in result.stdout


def test_refresh_help_includes_transport_option() -> None:
    result = runner.invoke(app, ["refresh", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "--verbose" in result.stdout
    assert "custom-http" in result.stdout


def test_probe_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["probe", "--help"])
    assert result.exit_code == 0
    assert "websocket" in result.stdout
    assert "summarize" in result.stdout


def test_probe_websocket_help_includes_required_options() -> None:
    result = runner.invoke(app, ["probe", "websocket", "--help"])
    assert result.exit_code == 0
    assert "--cdp-url" in result.stdout
    assert "--duration" in result.stdout
    assert "--output" in result.stdout


def test_probe_summarize_help_works() -> None:
    result = runner.invoke(app, ["probe", "summarize", "--help"])
    assert result.exit_code == 0
    assert "JSONL" in result.stdout


@pytest.mark.parametrize(
    ("args", "has_verbose"),
    [
        (["auth", "--help"], False),
        (["import-state", "--help"], False),
        (["import-posts", "--help"], False),
        (["probe", "websocket", "--help"], False),
        (["probe", "summarize", "--help"], False),
        (["index", "changed-only", "--help"], False),
        (["index", "full-reembed", "--help"], False),
        (["ask", "--help"], False),
    ],
)
def test_help_verbose_exposure(args: list[str], has_verbose: bool) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert ("--verbose" in result.stdout) is has_verbose
