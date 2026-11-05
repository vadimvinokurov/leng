"""Tests for the HTTP API: token auth, limits, and POST /v1/query."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.routes import create_app
from app.config import Config

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

API_TOKEN = "test-token"
AUTH_HEADERS = {"X-Api-Token": API_TOKEN}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def word_body(text: str = "__WORD_CASE__ throughput", client: str = "pytest") -> dict:
    return {
        "raw_text": text,
        "source": {
            "app": "safari",
            "title": "An article",
            "url": "https://example.com",
        },
        "client": client,
    }


@pytest.fixture
def api_env(tmp_path):
    """A token-bearing Config and the path of the database behind it."""
    db_path = tmp_path / "api.db"
    cfg = Config(
        api_token=API_TOKEN,
        db_path=str(db_path),
        llm_base_url="http://localhost:0",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_timeout_s=1.0,
    )
    return cfg, str(db_path)


def make_client(cfg, responses):
    """Build a TestClient from a Config and FakeLLM routing markers."""
    from app.llm.fake import FakeLLM

    llm = FakeLLM(responses)
    return TestClient(create_app(cfg, llm)), llm


def db_rows(db_path):
    """A separate connection to the app database with Row access."""
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_health_without_token_returns_ok(api_env):
    cfg, _ = api_env
    client, _ = make_client(cfg, {})

    resp = client.get("/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "dev"}


def test_health_reports_configured_version(api_env):
    cfg, _ = api_env
    cfg = Config(
        **{**cfg.__dict__, "version": "0.2.0"}
    )
    client, _ = make_client(cfg, {})

    resp = client.get("/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.2.0"}


def test_query_without_token_returns_401(api_env):
    cfg, _ = api_env
    client, _ = make_client(cfg, {})

    resp = client.post("/v1/query", json=word_body())

    assert resp.status_code == 401


def test_word_query_returns_shape_and_persists(api_env):
    cfg, db_path = api_env
    client, llm = make_client(cfg, {"__WORD_CASE__": load_fixture("word")})

    resp = client.post("/v1/query", json=word_body(), headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == {
        "query_id": 1,
        "kind": "word",
        "canonical": "throughput",
        "response": {
            "main": "пропускная способность",
            "notes": [
                "В контексте распределённых систем (Kafka) — объём сообщений,"
                " обрабатываемых в единицу времени; часто не переводится."
            ],
        },
        "soft_mode": False,
    }
    assert llm.calls == 1

    conn = db_rows(db_path)
    row = conn.execute(
        "SELECT id, kind, raw_text, source_app, source_title, source_url, client"
        " FROM queries"
    ).fetchone()
    assert row["id"] == 1
    assert row["kind"] == "word"
    assert row["raw_text"] == "__WORD_CASE__ throughput"
    assert row["source_app"] == "safari"
    assert row["source_title"] == "An article"
    assert row["source_url"] == "https://example.com"
    assert row["client"] == "pytest"
    conn.close()


def test_query_empty_text_returns_400_empty_text(api_env):
    cfg, db_path = api_env
    client, _ = make_client(cfg, {})

    resp = client.post("/v1/query", json=word_body(text="   "), headers=AUTH_HEADERS)

    assert resp.status_code == 400
    assert resp.json() == {"error": "empty_text"}
    conn = db_rows(db_path)
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0
    conn.close()


def test_query_text_too_long_returns_400_text_too_long(api_env):
    cfg, _ = api_env
    client, _ = make_client(cfg, {})

    resp = client.post(
        "/v1/query", json=word_body(text="x" * 50_001), headers=AUTH_HEADERS
    )

    assert resp.status_code == 400
    assert resp.json() == {"error": "text_too_long"}


def test_llm_garbage_returns_502_llm_error_and_no_rows(api_env):
    cfg, db_path = api_env
    responses = {
        "__WORD_CASE__": {"kind": "poem", "canonical": None, "translation": {}}
    }
    client, llm = make_client(cfg, responses)

    for _ in range(2):
        resp = client.post("/v1/query", json=word_body(), headers=AUTH_HEADERS)

        assert resp.status_code == 502
        assert resp.json() == {"error": "llm_error"}

    assert llm.calls == 2
    conn = db_rows(db_path)
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM word_entries").fetchone()[0] == 0
    conn.close()
