"""Naive reference implementation of the query pipeline (test oracle).

This module deliberately does NOT call process_query or any of its
internals (no cache lookup, no fast path, nothing shared beyond the data
classes and the LLM contract): it is a maximally direct, independent copy
of the LLM branch of the query pipeline. It always calls the LLM — there
is no translation cache to consult or write. The oracle test
(test_query_oracle.py) runs a request sequence through side-by-side
(conn, FakeLLM) pairs and asserts the externally visible results match,
which is what pins the cache fast path as a pure optimization.
"""

import json
import sqlite3
import time
from datetime import datetime

from pydantic import ValidationError

from app import prompts
from app.llm.base import LLMClient, LLMError, LLMQueryResponse
from app.service.query import QueryResult, Source
from app.service.words import upsert_word


def naive_process_query(
    conn: sqlite3.Connection,
    llm: LLMClient,
    text: str,
    source: Source,
    client_name: str,
    now: datetime,
) -> QueryResult:
    """Classify and translate `text`, then persist it — always via the LLM.

    The dumbest correct form: call the LLM, validate against the pinned
    response contract (a non-conforming payload raises LLMError having
    written nothing), insert the queries row, and — for kind word|phrase —
    upsert the word entry and link the query. `sentence`-like kinds leave
    no dictionary trace. latency_ms is measured from function entry and
    rounds sub-millisecond runs up to 1 so the latency_ms > 0 invariant
    holds.
    """
    started = time.monotonic()
    prompt = prompts.load("translate_and_classify")
    raw = llm.complete_json(prompt.splitlines()[0], prompt.format(text=text))
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
    query_id = cur.lastrowid

    if kind in ("word", "phrase"):
        upsert_word(conn, canonical, query_id, now)

    conn.commit()
    return QueryResult(
        query_id=query_id,
        kind=kind,
        canonical=canonical,
        response=response,
    )
