"""Shared harness for the golden API corpus.

`app_with_fixture` wires the production app against a fresh SQLite
database with a fixed clock and a FakeLLM routed by all five case
markers, so the golden generation run and the golden tests pin
byte-identical /v1/query responses.
"""

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.api.routes import create_app
from app.config import Config
from app.llm.fake import FakeLLM

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"
GOLDEN_DIR = Path(__file__).parent / "golden"

FIXED_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
API_TOKEN = "test-token"
AUTH_HEADERS = {"X-Api-Token": API_TOKEN}

# Caller-side routing: the raw_text marker steers FakeLLM to the fixture.
MARKERS = {
    "word": "__WORD_CASE__",
    "phrase": "__PHRASE_CASE__",
    "sentence": "__SENTENCE_CASE__",
    "paragraph": "__PARAGRAPH_CASE__",
    "fragment": "__FRAGMENT_CASE__",
}

KIND_TEXTS = {
    "word": "__WORD_CASE__ throughput",
    "phrase": "__PHRASE_CASE__ to be about to",
    "sentence": "__SENTENCE_CASE__ Kafka is fast.",
    "paragraph": "__PARAGRAPH_CASE__ Kafka is fast. "
                 "Consumers pull messages at their own pace.",
    "fragment": "__FRAGMENT_CASE__ amid",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def fixture_responses() -> dict[str, dict]:
    """All five case markers routed to their fixture payloads."""
    return {marker: load_fixture(kind) for kind, marker in MARKERS.items()}


def query_body(text: str) -> dict:
    """The pinned request body: same source/client shape for every kind."""
    return {
        "raw_text": text,
        "source": {"app": "golden-app", "title": None, "url": None},
        "client": "golden-runner",
    }


def app_with_fixture(
    kind_fixture: str, tmp_path: Path | None = None
) -> tuple[TestClient, FakeLLM, sqlite3.Connection]:
    """Build the app, its FakeLLM and a reader connection on a fresh DB.

    tmp_path defaults to a fresh system temp dir (the one-shot golden
    generation run); tests pass pytest's tmp_path for automatic cleanup.
    The caller owns the returned sqlite3 connection and must close it.
    """
    db_dir = (
        Path(tmp_path) if tmp_path is not None
        else Path(tempfile.mkdtemp(prefix="golden-"))
    )
    db_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        api_token=API_TOKEN,
        db_path=str(db_dir / "golden.db"),
        llm_base_url="http://localhost:0",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_timeout_s=1.0,
    )
    llm = FakeLLM(fixture_responses())
    client = TestClient(create_app(cfg, llm, clock=lambda: FIXED_NOW))
    conn = db.connect(str(db_dir / "golden.db"))
    conn.row_factory = sqlite3.Row
    return client, llm, conn
