"""Dictionary persistence: word_entries upserts with query links."""

import sqlite3
from datetime import datetime


def upsert_word(
    conn: sqlite3.Connection, canonical: str, query_id: int, now: datetime
) -> int:
    """Create or bump the word_entries row for canonical and link the query.

    First insert stores occurrences=1 with first_query_id/created_at pinned
    to this query; a conflicting canonical bumps occurrences and moves
    last_seen_at, leaving first_query_id and created_at untouched. The
    (query_id, word_id) link is inserted only when absent, so re-linking the
    same query is idempotent — the links table has no unique constraint.
    Returns the id of the word_entries row.
    """
    seen_at = now.isoformat()
    conn.execute(
        """
        INSERT INTO word_entries (canonical, first_query_id, created_at,
                                  occurrences, last_seen_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(canonical) DO UPDATE SET
          occurrences = occurrences + 1,
          last_seen_at = ?
        """,
        (canonical, query_id, seen_at, seen_at, seen_at),
    )
    # The upsert above guarantees the word_entries row exists; sqlite3's
    # type stubs cannot express that invariant.
    row = conn.execute(
        "SELECT id FROM word_entries WHERE canonical = ?", (canonical,)
    ).fetchone()
    assert row is not None
    word_id = int(row[0])
    already_linked = conn.execute(
        "SELECT 1 FROM query_word_links WHERE query_id = ? AND word_id = ?",
        (query_id, word_id),
    ).fetchone()
    if already_linked is None:
        conn.execute(
            "INSERT INTO query_word_links (query_id, word_id) VALUES (?, ?)",
            (query_id, word_id),
        )
    return word_id
