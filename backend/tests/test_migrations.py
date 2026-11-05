import sqlite3

from app import db

EXPECTED_TABLES = {
    "queries",
    "word_entries",
    "query_word_links",
    "translation_cache",
}


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def test_tables(conn):
    db.apply_migrations(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert names >= EXPECTED_TABLES


def test_table_columns(conn):
    db.apply_migrations(conn)
    assert _column_names(conn, "queries") == [
        "id", "created_at", "raw_text", "kind", "source_app",
        "source_title", "source_url", "client", "translation",
        "latency_ms", "mode",
    ]
    assert _column_names(conn, "word_entries") == [
        "id", "canonical", "first_query_id", "created_at",
        "occurrences", "last_seen_at", "status", "blocked_reason",
    ]
    assert _column_names(conn, "query_word_links") == [
        "query_id", "word_id",
    ]
    assert _column_names(conn, "translation_cache") == [
        "canonical", "lang", "translation_json", "updated_at",
    ]


def test_apply_migrations_is_idempotent(conn):
    db.apply_migrations(conn)
    db.apply_migrations(conn)


def test_indexes(conn):
    db.apply_migrations(conn)

    def index_columns(table: str) -> dict[str, list[str]]:
        result: dict[str, tuple[list[str], bool]] = {}
        for (name,) in conn.execute(
            "SELECT name FROM pragma_index_list(?)", (table,)
        ):
            (unique,)= conn.execute(
                "SELECT [unique] FROM pragma_index_list(?) WHERE name = ?",
                (table, name),
            ).fetchone()
            cols = [
                row[2]
                for row in conn.execute(f"PRAGMA index_info('{name}')")
            ]
            result[name] = (cols, bool(unique))
        return result

    queries_indexes = index_columns("queries")
    assert queries_indexes["idx_queries_created_at"] == (["created_at"], False)

    word_indexes = index_columns("word_entries")
    # UNIQUE(canonical) is backed by a unique index, verbatim.
    assert (["canonical"], True) in word_indexes.values()

    link_indexes = index_columns("query_word_links")
    assert link_indexes["idx_query_word_links_word_id"] == (
        ["word_id"], False,
    )


def test_connect_enables_wal_and_foreign_keys(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_word_entries_canonical_unique_enforced(conn):
    db.apply_migrations(conn)
    conn.execute(
        "INSERT INTO word_entries (canonical, first_query_id, created_at,"
        " last_seen_at) VALUES ('go', 1, '2026-01-01T00:00:00Z',"
        " '2026-01-01T00:00:00Z')"
    )
    try:
        conn.execute(
            "INSERT INTO word_entries (canonical, first_query_id, created_at,"
            " last_seen_at) VALUES ('go', 2, '2026-01-01T00:00:01Z',"
            " '2026-01-01T00:00:01Z')"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate canonical was accepted")
