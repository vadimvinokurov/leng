"""Tests for GET /v1/word/{word_id}/history: listing, auth, unknown id."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_app
from app.config import Config

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

API_TOKEN = "test-token"
AUTH_HEADERS = {"X-Api-Token": API_TOKEN}

WORD_FIXTURE = json.loads((FIXTURES_DIR / "word.json").read_text(encoding="utf-8"))
WORD_RESPONSE = WORD_FIXTURE["translation"]


def word_body(app: str) -> dict:
    return {
        "raw_text": "__WORD_CASE__ throughput",
        "source": {
            "app": app,
            "title": f"An article in {app}",
            "url": f"https://example.com/{app}",
        },
        "client": "pytest",
    }


@pytest.fixture
def api_env(tmp_path):
    """A token-bearing Config pointing at an empty database."""
    cfg = Config(
        api_token=API_TOKEN,
        db_path=str(tmp_path / "api.db"),
        llm_base_url="http://localhost:0",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_timeout_s=1.0,
    )
    return cfg


def make_client(cfg, responses):
    """Build a TestClient from a Config and FakeLLM routing markers."""
    from app.llm.fake import FakeLLM

    llm = FakeLLM(responses)
    return TestClient(create_app(cfg, llm)), llm


def test_word_history_two_queries_from_different_sources_desc(api_env):
    client, llm = make_client(api_env, {"__WORD_CASE__": WORD_FIXTURE})

    client.post("/v1/query", json=word_body("safari"), headers=AUTH_HEADERS)
    time.sleep(0.002)  # distinct created_at so the DESC order is deterministic
    client.post("/v1/query", json=word_body("firefox"), headers=AUTH_HEADERS)
    assert llm.calls == 1  # the repeat is served from translation_cache

    resp = client.get("/v1/word/1/history", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["word_id"] == 1
    assert body["canonical"] == "throughput"
    assert body["occurrences"] == 2
    assert len(body["history"]) == 2
    assert body["history"][0]["source_app"] == "firefox"
    assert body["history"][1]["source_app"] == "safari"
    for item in body["history"]:
        assert item["source_title"] == f"An article in {item['source_app']}"
        assert item["source_url"].endswith(item["source_app"])
        assert item["raw_text"] == "__WORD_CASE__ throughput"
        assert item["created_at"]
        assert item["translation"] == WORD_RESPONSE


def test_word_history_unknown_id_returns_404(api_env):
    client, _ = make_client(api_env, {})

    resp = client.get("/v1/word/99/history", headers=AUTH_HEADERS)

    assert resp.status_code == 404
    assert resp.json() == {"error": "word_not_found"}


def test_word_history_without_token_returns_401(api_env):
    client, _ = make_client(api_env, {})

    resp = client.get("/v1/word/1/history")

    assert resp.status_code == 401
