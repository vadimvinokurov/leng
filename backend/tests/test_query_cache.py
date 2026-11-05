"""Tests for the translation cache fast path in the query service."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from app.llm.fake import FakeLLM
from app.service.query import Source, process_query

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def make_service(conn, responses: dict[str, dict]):
    conn.row_factory = sqlite3.Row
    db.apply_migrations(conn)
    llm = FakeLLM(responses)
    source = Source(app="safari", title="An article", url="https://example.com")
    return llm, source


def test_word_repeat_is_served_from_cache_without_llm(conn):
    # The marker is the text itself, so a repeat query would still find a
    # configured response if the fast path were broken and called the LLM.
    llm, source = make_service(conn, {"throughput": load_fixture("word")})

    first = process_query(conn, llm, "throughput", source, "test-client", NOW)
    second = process_query(conn, llm, "throughput", source, "test-client", LATER)

    assert llm.calls == 1
    assert second.query_id != first.query_id
    assert second.kind == "word"
    assert second.canonical == "throughput"
    assert second.response == first.response
    assert second.soft_mode is False

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM queries"
    ).fetchone()["n"] == 2
    entry = conn.execute(
        "SELECT first_query_id, created_at, occurrences, last_seen_at"
        " FROM word_entries"
    ).fetchone()
    # first_query_id and created_at stay pinned to the first (LLM) query.
    assert entry["first_query_id"] == first.query_id
    assert entry["created_at"] == NOW.isoformat()
    assert entry["occurrences"] == 2
    assert entry["last_seen_at"] == LATER.isoformat()
    links = conn.execute(
        "SELECT query_id FROM query_word_links ORDER BY query_id"
    ).fetchall()
    assert [row["query_id"] for row in links] == [first.query_id, second.query_id]


def test_cache_key_is_case_insensitive_and_trimmed(conn):
    llm, source = make_service(
        conn,
        {"throughput": load_fixture("word"), "Throughput": load_fixture("word")},
    )

    process_query(conn, llm, "throughput", source, "test-client", NOW)
    second = process_query(
        conn, llm, "  Throughput  ", source, "test-client", LATER
    )

    assert llm.calls == 1
    assert second.kind == "word"
    assert second.canonical == "throughput"
    assert second.response == {
        "main": "пропускная способность",
        "notes": [
            "В контексте распределённых систем (Kafka) — объём сообщений, "
            "обрабатываемых в единицу времени; часто не переводится."
        ],
    }
    assert conn.execute(
        "SELECT occurrences AS n FROM word_entries"
    ).fetchone()["n"] == 2


def test_cache_hit_for_phrase_reports_kind_phrase(conn):
    llm, source = make_service(conn, {"to be about to": load_fixture("phrase")})

    process_query(conn, llm, "to be about to", source, "test-client", NOW)
    second = process_query(
        conn, llm, "to be about to", source, "test-client", LATER
    )

    assert llm.calls == 1
    assert second.kind == "phrase"
    assert second.canonical == "to be about to"
    assert second.response == {
        "main": "собираться сделать что-то",
        "notes": [],
    }


def test_llm_path_writes_translation_cache(conn):
    llm, source = make_service(conn, {"throughput": load_fixture("word")})

    result = process_query(conn, llm, "throughput", source, "test-client", NOW)

    row = conn.execute(
        "SELECT canonical, lang, translation_json, updated_at"
        " FROM translation_cache"
    ).fetchone()
    assert row is not None
    assert row["canonical"] == "throughput"
    assert row["lang"] == "en->ru"
    # The payload carries the LLM canonical inside so the fast path can
    # serve lemmatized repeats; the API response keeps {main, notes}.
    assert json.loads(row["translation_json"]) == {
        "canonical": "throughput",
        "main": "пропускная способность",
        "notes": [
            "В контексте распределённых систем (Kafka) — объём сообщений, "
            "обрабатываемых в единицу времени; часто не переводится."
        ],
    }
    assert result.response == {
        "main": "пропускная способность",
        "notes": [
            "В контексте распределённых систем (Kafka) — объём сообщений, "
            "обрабатываемых в единицу времени; часто не переводится."
        ],
    }
    assert row["updated_at"] == NOW.isoformat()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM translation_cache"
    ).fetchone()["n"] == 1


def test_lemmatized_repeat_is_served_from_cache(conn):
    # "went" lemmatizes to canonical "go": the fast path must still fire,
    # keyed on the normalized query text, not on the word entry canonical.
    llm, source = make_service(conn, {"went": load_fixture("went")})

    first = process_query(conn, llm, "went", source, "test-client", NOW)
    second = process_query(conn, llm, "went", source, "test-client", LATER)

    assert first.canonical == "go"
    assert llm.calls == 1
    assert second.kind == "word"
    assert second.canonical == "go"
    assert second.response == first.response

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM queries"
    ).fetchone()["n"] == 2
    entry = conn.execute(
        "SELECT canonical, occurrences, last_seen_at FROM word_entries"
    ).fetchone()
    assert entry["canonical"] == "go"
    assert entry["occurrences"] == 2
    assert entry["last_seen_at"] == LATER.isoformat()
    links = conn.execute(
        "SELECT query_id FROM query_word_links ORDER BY query_id"
    ).fetchall()
    assert [row["query_id"] for row in links] == [first.query_id, second.query_id]


def test_legacy_cache_row_without_canonical_is_treated_as_a_miss(conn):
    # A cache row written before the payload carried "canonical" must be
    # re-translated once and rewritten in the new payload format.
    llm, source = make_service(conn, {"throughput": load_fixture("word")})
    conn.execute(
        """
        INSERT INTO word_entries (canonical, first_query_id, created_at,
                                  occurrences, last_seen_at)
        VALUES ('throughput', 0, ?, 1, ?)
        """,
        (NOW.isoformat(), NOW.isoformat()),
    )
    legacy_payload = {"main": "старый перевод", "notes": []}
    conn.execute(
        """
        INSERT INTO translation_cache (canonical, lang, translation_json,
                                       updated_at)
        VALUES ('throughput', 'en->ru', ?, ?)
        """,
        (json.dumps(legacy_payload), NOW.isoformat()),
    )
    conn.commit()

    result = process_query(conn, llm, "throughput", source, "test-client", NOW)

    assert llm.calls == 1
    payload = json.loads(
        conn.execute(
            "SELECT translation_json FROM translation_cache"
        ).fetchone()[0]
    )
    assert payload == {
        "canonical": "throughput",
        "main": "пропускная способность",
        "notes": [
            "В контексте распределённых систем (Kafka) — объём сообщений, "
            "обрабатываемых в единицу времени; часто не переводится."
        ],
    }
    assert result.response == {
        "main": "пропускная способность",
        "notes": [
            "В контексте распределённых систем (Kafka) — объём сообщений, "
            "обрабатываемых в единицу времени; часто не переводится."
        ],
    }
    # The delete-then-insert rewrite keeps one row per key.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM translation_cache"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT occurrences AS n FROM word_entries"
    ).fetchone()["n"] == 2


def test_cache_row_without_word_entry_falls_through_to_llm(conn):
    # A cache hit whose dictionary entry is absent must not error: it
    # falls through to the ordinary LLM path.
    llm, source = make_service(conn, {"go": load_fixture("went")})
    conn.execute(
        """
        INSERT INTO translation_cache (canonical, lang, translation_json,
                                       updated_at)
        VALUES ('go', 'en->ru', ?, ?)
        """,
        (
            json.dumps(
                {
                    "canonical": "go",
                    "main": "идти",
                    "notes": ["Прошедшая форма — went; неправильный глагол."],
                }
            ),
            NOW.isoformat(),
        ),
    )
    conn.commit()

    result = process_query(conn, llm, "go", source, "test-client", NOW)

    assert llm.calls == 1
    assert result.canonical == "go"
    entry = conn.execute(
        "SELECT canonical, occurrences FROM word_entries"
    ).fetchone()
    assert entry["canonical"] == "go"
    assert entry["occurrences"] == 1
    payload = json.loads(
        conn.execute(
            "SELECT translation_json FROM translation_cache"
        ).fetchone()[0]
    )
    assert payload["canonical"] == "go"


def test_cache_row_with_null_canonical_is_treated_as_a_miss(conn):
    # A corrupt/legacy row carrying {"canonical": null} passes the current
    # '"canonical" in payload' key check — the key IS present, it is null.
    # It must be treated as a miss (LLM re-translates and rewrites), not
    # crash the query with a TypeError.
    llm, source = make_service(conn, {"throughput": load_fixture("word")})
    conn.execute(
        """
        INSERT INTO word_entries (canonical, first_query_id, created_at,
                                  occurrences, last_seen_at)
        VALUES ('throughput', 0, ?, 1, ?)
        """,
        (NOW.isoformat(), NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO translation_cache (canonical, lang, translation_json,
                                       updated_at)
        VALUES ('throughput', 'en->ru', ?, ?)
        """,
        (json.dumps({"canonical": None, "main": "мусор", "notes": []}),
         NOW.isoformat()),
    )
    conn.commit()

    result = process_query(conn, llm, "throughput", source, "test-client", NOW)

    assert llm.calls == 1
    assert result.canonical == "throughput"
    payload = json.loads(
        conn.execute(
            "SELECT translation_json FROM translation_cache"
        ).fetchone()[0]
    )
    assert payload["canonical"] == "throughput"
    assert payload["main"] == "пропускная способность"


def test_sentence_queries_never_touch_the_cache(conn):
    llm, source = make_service(conn, {"Kafka is fast.": load_fixture("sentence")})

    process_query(conn, llm, "Kafka is fast.", source, "test-client", NOW)
    process_query(conn, llm, "Kafka is fast.", source, "test-client", LATER)

    assert llm.calls == 2
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM translation_cache"
    ).fetchone()["n"] == 0
