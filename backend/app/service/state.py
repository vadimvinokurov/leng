"""State snapshot: the full read model served by GET /v1/state."""

import json
import sqlite3
from typing import Any


def snapshot(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Dump every table (rule 6): queries, words, links and the cache.

    `translation` is parsed from the stored JSON string (internal writes
    always store a JSON object; on parse failure the error propagates).
    `translation_json` in the cache is served exactly as stored. Rows are
    ordered deterministically everywhere. Uses cursor-local row factories
    so the caller's connection keeps its own.
    """
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    queries = [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "raw_text": row["raw_text"],
            "kind": row["kind"],
            "source_app": row["source_app"],
            "source_title": row["source_title"],
            "source_url": row["source_url"],
            "client": row["client"],
            "translation": json.loads(row["translation"]),
            "latency_ms": row["latency_ms"],
            "mode": row["mode"],
        }
        for row in cur.execute(
            """
            SELECT id, created_at, raw_text, kind, source_app, source_title,
                   source_url, client, translation, latency_ms, mode
            FROM queries ORDER BY id
            """
        )
    ]
    words = [
        {
            "id": row["id"],
            "canonical": row["canonical"],
            "first_query_id": row["first_query_id"],
            "created_at": row["created_at"],
            "occurrences": row["occurrences"],
            "last_seen_at": row["last_seen_at"],
            "status": row["status"],
            "blocked_reason": row["blocked_reason"],
        }
        for row in cur.execute(
            """
            SELECT id, canonical, first_query_id, created_at, occurrences,
                   last_seen_at, status, blocked_reason
            FROM word_entries ORDER BY id
            """
        )
    ]
    links = [
        {"query_id": row["query_id"], "word_id": row["word_id"]}
        for row in cur.execute(
            "SELECT query_id, word_id FROM query_word_links ORDER BY query_id, word_id"
        )
    ]
    cache = [
        {
            "canonical": row["canonical"],
            "lang": row["lang"],
            "translation_json": row["translation_json"],
        }
        for row in cur.execute(
            """
            SELECT canonical, lang, translation_json
            FROM translation_cache ORDER BY canonical
            """
        )
    ]
    return {"queries": queries, "words": words, "links": links, "cache": cache}
