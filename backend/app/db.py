"""SQLite wiring: connection setup and SQL-file migrations."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with WAL journaling and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply not-yet-applied migrations from app/migrations in filename order.

    Each migration runs inside a single transaction together with its
    row in the tracked schema_migrations table, so a partially applied
    migration is never visible.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        );
        """
    )
    applied = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations")
    }
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", maxsplit=1)[0])
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        applied_at = datetime.now(UTC).isoformat(timespec="seconds")
        # The migration SQL and its schema_migrations row still commit
        # together: BEGIN is opened here, the insert below joins the same
        # transaction, and the commit lands when both succeeded.
        conn.executescript(f"BEGIN;\n{sql}\n")
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)\n"
            "VALUES (?, ?, ?)",
            (version, path.name, applied_at),
        )
        conn.commit()
