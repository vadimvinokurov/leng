"""Tests for the query service: classify + translate + dictionary upsert."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import db
from app.llm.base import LLMError
from app.llm.fake import FakeLLM
from app.service import persona
from app.service.query import SYSTEM_PROMPT, Source, process_query

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)

WORD_TEXT = "__WORD_CASE__ throughput"
PHRASE_TEXT = "__PHRASE_CASE__ to be about to"
SENTENCE_TEXT = "__SENTENCE_CASE__ Kafka is fast."


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def make_service(conn, responses: dict[str, dict]):
    conn.row_factory = sqlite3.Row
    db.apply_migrations(conn)
    llm = FakeLLM(responses)
    source = Source(app="safari", title="An article", url="https://example.com")
    return llm, source


def test_word_query_persists_query_word_and_link(conn):
    llm, source = make_service(conn, {"__WORD_CASE__": load_fixture("word")})

    result = process_query(conn, llm, WORD_TEXT, source, "test-client", NOW)

    assert result.kind == "word"
    assert result.canonical == "throughput"
    assert result.soft_mode is False
    assert result.response == {
        "main": "пропускная способность",
        "notes": ["В контексте распределённых систем (Kafka) — объём сообщений, "
                  "обрабатываемых в единицу времени; часто не переводится."],
    }
    assert llm.calls == 1

    row = conn.execute(
        "SELECT id, created_at, raw_text, kind, source_app, source_title,"
        " source_url, client, translation, latency_ms, mode FROM queries"
    ).fetchone()
    assert row["id"] == result.query_id
    assert row["created_at"] == NOW.isoformat()
    assert row["raw_text"] == WORD_TEXT
    assert row["kind"] == "word"
    assert row["source_app"] == "safari"
    assert row["source_title"] == "An article"
    assert row["source_url"] == "https://example.com"
    assert row["client"] == "test-client"
    assert json.loads(row["translation"]) == result.response
    assert row["latency_ms"] > 0
    assert row["mode"] == "instant"

    entry = conn.execute(
        "SELECT id, canonical, first_query_id, created_at, occurrences, last_seen_at"
        " FROM word_entries"
    ).fetchone()
    assert entry["canonical"] == "throughput"
    assert entry["first_query_id"] == result.query_id
    assert entry["created_at"] == NOW.isoformat()
    assert entry["occurrences"] == 1
    assert entry["last_seen_at"] == NOW.isoformat()

    link = conn.execute(
        "SELECT query_id, word_id FROM query_word_links"
    ).fetchone()
    assert link["query_id"] == result.query_id
    assert link["word_id"] == entry["id"]


def test_repeat_query_bumps_occurrences_and_relinks(conn):
    llm, source = make_service(conn, {"__WORD_CASE__": load_fixture("word")})

    first = process_query(conn, llm, WORD_TEXT, source, "test-client", NOW)
    second = process_query(conn, llm, WORD_TEXT, source, "test-client", LATER)

    assert second.query_id != first.query_id
    # Since the translation cache fast path (Task 5), a repeat of the same
    # text is served from the cache: one LLM call for both queries. The
    # remaining assertions prove the dictionary upsert still happens.
    assert llm.calls == 1

    entries = conn.execute(
        "SELECT canonical, occurrences, created_at, last_seen_at FROM word_entries"
    ).fetchall()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["canonical"] == "throughput"
    assert entry["occurrences"] == 2
    # first_query_id and created_at stay pinned to the first query.
    assert conn.execute(
        "SELECT first_query_id FROM word_entries"
    ).fetchone()["first_query_id"] == first.query_id
    assert entry["created_at"] == NOW.isoformat()
    assert entry["last_seen_at"] == LATER.isoformat()

    links = conn.execute(
        "SELECT query_id, word_id FROM query_word_links ORDER BY query_id"
    ).fetchall()
    assert len(links) == 2
    assert links[0]["query_id"] == first.query_id
    assert links[1]["query_id"] == second.query_id
    assert links[0]["word_id"] == links[1]["word_id"]


def test_phrase_query_creates_single_card(conn):
    llm, source = make_service(conn, {"__PHRASE_CASE__": load_fixture("phrase")})

    result = process_query(conn, llm, PHRASE_TEXT, source, "test-client", NOW)

    assert result.kind == "phrase"
    assert result.canonical == "to be about to"
    assert conn.execute("SELECT COUNT(*) AS n FROM queries").fetchone()["n"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM word_entries"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT occurrences FROM word_entries"
    ).fetchone()["occurrences"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM query_word_links"
    ).fetchone()["n"] == 1


def test_sentence_query_persists_no_words(conn):
    llm, source = make_service(conn, {"__SENTENCE_CASE__": load_fixture("sentence")})

    result = process_query(conn, llm, SENTENCE_TEXT, source, "test-client", NOW)

    assert result.kind == "sentence"
    assert result.canonical is None
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM word_entries"
    ).fetchone()["n"] == 0
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM query_word_links"
    ).fetchone()["n"] == 0
    row = conn.execute(
        "SELECT kind, raw_text, translation FROM queries"
    ).fetchone()
    assert row["kind"] == "sentence"
    assert row["raw_text"] == SENTENCE_TEXT
    assert json.loads(row["translation"]) == {"main": "цельный перевод", "notes": []}


def test_invalid_llm_payload_raises_llm_error_without_persisting(conn):
    llm, source = make_service(
        conn,
        {"__WORD_CASE__": {"kind": "poem", "canonical": None, "translation": {}}},
    )

    with pytest.raises(LLMError):
        process_query(conn, llm, WORD_TEXT, source, "test-client", NOW)

    assert conn.execute("SELECT COUNT(*) AS n FROM queries").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM word_entries").fetchone()["n"] == 0


def test_persona_file_is_appended_to_system_prompt(conn, monkeypatch, tmp_path):
    persona_text = (
        "The user is a C++ game engine programmer"
        " working on neural image compression research."
    )
    persona_file = tmp_path / "persona.md"
    persona_file.write_text(persona_text, encoding="utf-8")
    monkeypatch.setenv(persona.PERSONA_PATH_ENV, str(persona_file))
    llm, source = make_service(conn, {"__SENTENCE_CASE__": load_fixture("sentence")})

    process_query(conn, llm, SENTENCE_TEXT, source, "test-client", NOW)

    assert llm.last_system == f"{SYSTEM_PROMPT}\n\n{persona_text}"


def test_missing_persona_file_keeps_plain_system_prompt(conn, monkeypatch, tmp_path):
    monkeypatch.setenv(persona.PERSONA_PATH_ENV, str(tmp_path / "absent.md"))
    llm, source = make_service(conn, {"__SENTENCE_CASE__": load_fixture("sentence")})

    process_query(conn, llm, SENTENCE_TEXT, source, "test-client", NOW)

    assert llm.last_system == SYSTEM_PROMPT
