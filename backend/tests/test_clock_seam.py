"""Tests for the clock seam: POST /v1/query persists the injected time."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.routes import create_app
from app.config import Config

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

API_TOKEN = "test-token"
AUTH_HEADERS = {"X-Api-Token": API_TOKEN}

FIXED_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


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


def make_client(cfg, responses, clock=None):
    """Build a TestClient from a Config, FakeLLM markers and an optional clock."""
    from app.llm.fake import FakeLLM

    llm = FakeLLM(responses)
    app = create_app(cfg, llm) if clock is None else create_app(cfg, llm, clock=clock)
    return TestClient(app), llm


def db_rows(db_path):
    """A separate connection to the app database with Row access."""
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_query_with_injected_clock_persists_fixed_time(api_env):
    cfg, db_path = api_env
    client, _ = make_client(
        cfg, {"__WORD_CASE__": load_fixture("word")}, clock=lambda: FIXED_NOW
    )

    resp = client.post("/v1/query", json=word_body(), headers=AUTH_HEADERS)
    assert resp.status_code == 200

    conn = db_rows(db_path)
    created_at = conn.execute("SELECT created_at FROM queries").fetchone()[0]
    conn.close()
    assert created_at == "2026-11-05T12:00:00+00:00"


def test_query_without_clock_persists_real_utc_now(api_env):
    cfg, db_path = api_env
    client, _ = make_client(cfg, {"__WORD_CASE__": load_fixture("word")})

    resp = client.post("/v1/query", json=word_body(), headers=AUTH_HEADERS)
    assert resp.status_code == 200

    conn = db_rows(db_path)
    created_at = conn.execute("SELECT created_at FROM queries").fetchone()[0]
    conn.close()
    parsed = datetime.fromisoformat(created_at)
    assert parsed.tzinfo is not None
    assert abs(parsed - datetime.now(UTC)).total_seconds() < 60
