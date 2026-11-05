"""Oracle test: the cache fast path is externally equal to the naive path.

`tests/oracles.py` holds a maximally direct reference implementation that
always calls the LLM. This test runs a five-request sequence through both
implementations on separate (conn, FakeLLM) pairs and asserts the results
match everywhere the cache is supposed to be invisible.

Note on the sequence texts: the word queries carry the suite's
__WORD_CASE__ routing marker (see test_query_service.py) because
FakeLLM routes on substrings of the whole user prompt — and the prompt
template itself mentions "throughput" and "went" in its examples, so a
bare word text cannot be routed deterministically.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from oracles import naive_process_query

from app import db
from app.llm.fake import FakeLLM
from app.service.query import Source, process_query

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


def load_word_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "word.json").read_text(encoding="utf-8"))


def load_went_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "went.json").read_text(encoding="utf-8"))


def load_sentence_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "sentence.json").read_text(encoding="utf-8"))


SEQUENCE = [
    "__WORD_CASE__ throughput",
    "__WORD_CASE__ throughput",
    "__WORD_CASE__ Throughput",
    "__SENTENCE_CASE__ Kafka is fast.",
    "__WORD_CASE__ went",
]

RESPONSES = {
    "__WORD_CASE__ throughput": load_word_fixture(),
    "__WORD_CASE__ Throughput": load_word_fixture(),
    "__WORD_CASE__ went": load_went_fixture(),
    "__SENTENCE_CASE__ Kafka is fast.": load_sentence_fixture(),
}


def make_db(name: str, tmp_path) -> sqlite3.Connection:
    connection = db.connect(str(tmp_path / name))
    connection.row_factory = sqlite3.Row
    db.apply_migrations(connection)
    return connection


def row_fields(row: sqlite3.Row) -> tuple:
    """The queries columns compared pairwise; latency_ms is nondeterministic."""
    return (
        row["created_at"],
        row["raw_text"],
        row["kind"],
        row["source_app"],
        row["source_title"],
        row["source_url"],
        row["client"],
        row["translation"],
    )


def occurrences(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT canonical, occurrences FROM word_entries ORDER BY canonical"
    ).fetchall()
    return {row["canonical"]: row["occurrences"] for row in rows}


def test_naive_and_fast_paths_agree_over_a_five_request_sequence(tmp_path):
    naive_conn = make_db("naive.db", tmp_path)
    fast_conn = make_db("fast.db", tmp_path)
    naive_llm = FakeLLM(RESPONSES)
    fast_llm = FakeLLM(RESPONSES)
    source = Source(app="safari", title="An article", url="https://example.com")

    naive_results = [
        naive_process_query(naive_conn, naive_llm, text, source, "test-client", NOW)
        for text in SEQUENCE
    ]
    fast_results = [
        process_query(fast_conn, fast_llm, text, source, "test-client", NOW)
        for text in SEQUENCE
    ]

    # (c) The optimization is visible: the naive path calls the LLM for
    # every request; the fast path only on the cache misses (q1 word, q4
    # sentence, q5 "went" — q2 and q3 share q1's normalized cache key).
    assert naive_llm.calls == 5
    assert fast_llm.calls == 3

    # (d) Same request count, and (b) pairwise row equality.
    assert len(naive_results) == len(fast_results) == 5
    assert len(naive_conn.execute("SELECT * FROM queries").fetchall()) == 5
    assert len(fast_conn.execute("SELECT * FROM queries").fetchall()) == 5
    for naive_result, fast_result in zip(naive_results, fast_results, strict=True):
        assert fast_result.kind == naive_result.kind
        assert fast_result.canonical == naive_result.canonical
        assert fast_result.response == naive_result.response
        assert fast_result.query_id == naive_result.query_id
        naive_row = naive_conn.execute(
            "SELECT * FROM queries WHERE id = ?", (naive_result.query_id,)
        ).fetchone()
        fast_row = fast_conn.execute(
            "SELECT * FROM queries WHERE id = ?", (fast_result.query_id,)
        ).fetchone()
        assert row_fields(fast_row) == row_fields(naive_row)

    # (a) Final occurrences per canonical equal across both DBs: the three
    # "throughput" spellings (two served from cache) and the "went" -> "go"
    # lemma; the sentence query creates no word entry on either side.
    assert occurrences(naive_conn) == {"throughput": 3, "go": 1}
    assert occurrences(fast_conn) == occurrences(naive_conn)
