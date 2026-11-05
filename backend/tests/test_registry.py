"""Tests for the command registry: pinned ids, shapes, and the POST guard."""

from pathlib import Path

import pytest

from app.api.routes import create_app
from app.commands import registry
from app.commands.registry import COMMANDS, Command
from app.config import Config

API_TOKEN = "test-token"


def make_cfg(tmp_path: Path) -> Config:
    return Config(
        api_token=API_TOKEN,
        db_path=str(tmp_path / "registry.db"),
        llm_base_url="http://localhost:0",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_timeout_s=1.0,
    )


def test_registry_pins_command_ids():
    assert [c.id for c in COMMANDS] == [
        "health",
        "downloads_page",
        "client_manifest",
        "client_file",
        "client_refresh",
        "query",
        "word_history",
        "commands",
        "state",
    ]


def test_registry_entries_carry_method_path_and_auth():
    by_id = {c.id: c for c in COMMANDS}
    health, query, word_history = (
        by_id["health"],
        by_id["query"],
        by_id["word_history"],
    )
    commands, state = by_id["commands"], by_id["state"]
    downloads = by_id["downloads_page"]
    manifest, client_file, client_refresh = (
        by_id["client_manifest"],
        by_id["client_file"],
        by_id["client_refresh"],
    )

    assert (health.method, health.path, health.auth) == (
        "GET",
        "/v1/health",
        False,
    )
    assert (query.method, query.path, query.auth) == (
        "POST",
        "/v1/query",
        True,
    )
    assert (word_history.method, word_history.path, word_history.auth) == (
        "GET",
        "/v1/word/{word_id}/history",
        True,
    )
    assert (commands.method, commands.path, commands.auth) == (
        "GET",
        "/v1/commands",
        False,
    )
    assert (state.method, state.path, state.auth) == ("GET", "/v1/state", True)
    assert state.params is None
    assert (downloads.method, downloads.path, downloads.auth) == (
        "GET",
        "/",
        False,
    )
    assert (manifest.method, manifest.path, manifest.auth) == (
        "GET",
        "/v1/clients/latest.json",
        False,
    )
    assert (client_file.method, client_file.path, client_file.auth) == (
        "GET",
        "/v1/clients/files/{name:path}",
        False,
    )
    assert (client_refresh.method, client_refresh.path, client_refresh.auth) == (
        "POST",
        "/v1/clients/refresh",
        True,
    )


def test_registry_render_kinds():
    by_id = {c.id: c for c in COMMANDS}

    assert by_id["downloads_page"].renders == "html"
    assert by_id["client_file"].renders == "file"
    assert by_id["health"].renders == "json"


def test_registry_query_declares_params_schema():
    query = next(c for c in COMMANDS if c.id == "query")

    assert query.params is not None
    assert "raw_text" in query.params.model_json_schema()["properties"]


def test_post_command_without_params_raises_value_error(tmp_path, monkeypatch):
    broken = Command(
        id="broken_post",
        method="POST",
        path="/v1/broken",
        params=None,
        auth=True,
        builder=registry.build_health,
    )
    monkeypatch.setattr(registry, "COMMANDS", (broken,))

    from app.llm.fake import FakeLLM

    with pytest.raises(ValueError, match="broken_post"):
        create_app(make_cfg(tmp_path), FakeLLM({}))
