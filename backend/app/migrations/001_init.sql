-- 001_init.sql: initial MVP-1 schema (PROJECT.md §5).
-- Enforced by inline UNIQUE constraint (SQLite creates a unique index
-- for it, e.g. sqlite_autoindex_word_entries_1).
CREATE TABLE queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  kind TEXT NOT NULL,
  source_app TEXT NOT NULL,
  source_title TEXT,
  source_url TEXT,
  client TEXT NOT NULL,
  translation TEXT NOT NULL,      -- JSON: {"main": str, "notes": [str]}
  latency_ms INTEGER NOT NULL,
  mode TEXT NOT NULL DEFAULT 'instant'
);
CREATE TABLE word_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical TEXT NOT NULL UNIQUE,
  first_query_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  blocked_reason TEXT
);
CREATE TABLE query_word_links (
  query_id INTEGER NOT NULL,
  word_id INTEGER NOT NULL
);
CREATE TABLE translation_cache (
  canonical TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT 'en->ru',
  translation_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_queries_created_at ON queries(created_at);
CREATE INDEX idx_query_word_links_word_id ON query_word_links(word_id);