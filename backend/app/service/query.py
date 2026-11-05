"""Query service: classify + translate a query in one LLM call and persist it."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app import prompts
from app.llm.base import LLMClient, LLMError, LLMQueryResponse
from app.service.words import upsert_word

# Derived from the template so the two cannot silently diverge when the
# prompt file is edited.
SYSTEM_PROMPT = prompts.load("translate_and_classify").splitlines()[0]


@dataclass
class Source:
    app: str
    title: str | None = None
    url: str | None = None


@dataclass
class QueryResult:
    query_id: int
    kind: str
    canonical: str | None
    response: dict[str, Any]
    soft_mode: bool = False


def process_query(
    conn: sqlite3.Connection,
    llm: LLMClient,
    text: str,
    source: Source,
    client_name: str,
    now: datetime,
) -> QueryResult:
    """Classify and translate `text`, then persist it.

    For word/phrase queries the translation cache fast path fires: the
    normalized query text (`text.strip().lower()`) has a translation_cache
    row whose payload carries an LLM-canonical backed by a word_entries
    row -> the cached translation is served and the LLM is not called,
    with the payload's canonical as the dictionary form. A cache row in
    the legacy format (no "canonical" in the payload) or whose canonical
    lost its word_entries row is a miss: the LLM path re-translates and
    (for the legacy case) rewrites the row. Only word/phrase queries
    create word_entries rows, so this fast path never serves
    sentence-like texts — their cache never participates. The fact of the
    request is always saved: both paths insert a queries row, and a cache
    hit still bumps the word entry (occurrences / link / last_seen_at).

    When the LLM path runs, the response is validated against the pinned
    LLM response contract; a payload that does not validate is an LLMError
    (nothing is written). Word/phrase responses are also written to the
    translation cache (delete-then-insert of the latest payload keyed by
    the normalized text, with the canonical inside the payload).
    """
    started = time.monotonic()
    norm_key = text.strip().lower()

    # canonical is str | None only so sentence-like kinds can carry none;
    # for word|phrase it is guaranteed non-None (see the use below).
    kind: str
    canonical: str | None
    response: dict[str, Any]
    cached = _cache_hit(conn, norm_key)
    if cached is not None:
        kind, canonical, response = cached
    else:
        user_prompt = prompts.load("translate_and_classify").format(text=text)
        raw = llm.complete_json(SYSTEM_PROMPT, user_prompt)
        try:
            validated = LLMQueryResponse.model_validate(raw)
        except ValidationError as exc:
            raise LLMError(f"LLM response does not match the schema: {exc}") from exc

        response = {
            "main": validated.translation.main,
            "notes": list(validated.translation.notes),
        }
        kind = validated.kind
        canonical = validated.canonical

    query_id = _insert_query(
        conn, text, kind, response, source, client_name, started, now
    )

    if kind in ("word", "phrase"):
        # Both paths guarantee a str canonical for word|phrase: the pinned
        # contract's validator rejects None there, and _cache_hit's own
        # isinstance check does the same for the cache fast path.
        assert canonical is not None
        upsert_word(conn, canonical, query_id, now)
        if cached is None:
            # translation_cache has no unique constraint on canonical, so
            # "INSERT OR REPLACE by key" is expressed as delete-then-insert.
            conn.execute(
                "DELETE FROM translation_cache WHERE canonical = ?", (norm_key,)
            )
            conn.execute(
                """INSERT INTO translation_cache
                        (canonical, lang, translation_json, updated_at)
                   VALUES (?, 'en->ru', ?, ?)""",
                (
                    norm_key,
                    json.dumps({"canonical": canonical, **response}),
                    now.isoformat(),
                ),
            )

    conn.commit()
    return QueryResult(
        query_id=query_id,
        kind=kind,
        canonical=canonical,
        response=response,
    )


def _cache_hit(
    conn: sqlite3.Connection, norm_key: str
) -> tuple[str, str, dict[str, Any]] | None:
    """Return (kind, canonical, response) from translation_cache, or None.

    The cache key is the normalized query text, stored in the
    translation_cache.canonical column; the column name is historical —
    it holds this key, not a lemma. The payload carries the LLM-decided
    canonical inside (`{"canonical": ..., "main": ..., "notes": [...]}`)
    so lemmatized repeats ("went" -> "go") are still served: the returned
    canonical is the payload's, and kind is derived from it (a space means
    "phrase", no space means "word"). The key `{"main", "notes"}` response
    is built by the caller contract in process_query.

    Two miss-out conditions: a row written before the payload carried
    "canonical" (legacy format — the LLM re-translates and rewrites it),
    a payload whose "canonical" is present but not a string (null in a
    corrupt/legacy row — the key IS in the payload, so the key check
    above cannot catch it), and a payload whose canonical has no
    word_entries row (falls through instead of erroring).
    """
    row = conn.execute(
        "SELECT translation_json FROM translation_cache WHERE canonical = ?",
        (norm_key,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    if not isinstance(payload, dict) or not isinstance(
        payload.get("canonical"), str
    ):
        return None
    canonical = payload["canonical"]
    kind = "phrase" if " " in canonical else "word"
    # The payload was validated when written; re-validate defensively
    # (cheap) so a corrupt cache row surfaces as an LLMError, not a
    # malformed response.
    try:
        validated = LLMQueryResponse.model_validate(
            {"kind": kind, "canonical": canonical, "translation": payload}
        )
    except ValidationError as exc:
        raise LLMError(f"cached translation does not match the schema: {exc}") from exc
    entry = conn.execute(
        "SELECT 1 FROM word_entries WHERE canonical = ?", (canonical,)
    ).fetchone()
    if entry is None:
        return None
    return (
        kind,
        canonical,
        {"main": validated.translation.main,
         "notes": list(validated.translation.notes)},
    )


def _insert_query(
    conn: sqlite3.Connection,
    text: str,
    kind: str,
    response: dict[str, Any],
    source: Source,
    client_name: str,
    started: float,
    now: datetime,
) -> int:
    """Insert the queries row for one processed query and return its id.

    Latency is measured from `started` (function entry) and stored in
    whole milliseconds; sub-millisecond runs round to 1 so the
    latency_ms > 0 invariant holds.
    """
    latency_ms = max(1, round((time.monotonic() - started) * 1000))
    cur = conn.execute(
        """
        INSERT INTO queries (created_at, raw_text, kind, source_app,
                             source_title, source_url, client, translation,
                             latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now.isoformat(),
            text,
            kind,
            source.app,
            source.title,
            source.url,
            client_name,
            json.dumps(response),
            latency_ms,
        ),
    )
    # An INSERT into a rowid table always sets lastrowid on success; the
    # sqlite3 stubs type it Optional and cannot express that invariant.
    lastrowid = cur.lastrowid
    assert lastrowid is not None
    return lastrowid
