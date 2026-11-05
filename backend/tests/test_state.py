"""Tests for GET /v1/state: the headless full-snapshot endpoint (rule 6)."""

import json

import golden_helpers as gh

WORD_FIXTURE = gh.load_fixture("word")
PHRASE_FIXTURE = gh.load_fixture("phrase")
SENTENCE_FIXTURE = gh.load_fixture("sentence")


def run_queries(kinds, tmp_path):
    """A harness app on a fresh DB, the given kinds queried in order."""
    client, llm, conn = gh.app_with_fixture("word", tmp_path)
    conn.close()
    for kind in kinds:
        resp = client.post(
            "/v1/query",
            json=gh.query_body(gh.KIND_TEXTS[kind]),
            headers=gh.AUTH_HEADERS,
        )
        assert resp.status_code == 200
    return client, llm


def mask_latency(snapshot: dict) -> dict:
    # latency_ms is the only nondeterministic field in the snapshot.
    for query in snapshot["queries"]:
        assert query["latency_ms"] > 0
        query["latency_ms"] = 0
    return snapshot


def get_state(client):
    return client.get("/v1/state", headers=gh.AUTH_HEADERS)


def test_state_after_one_word_query_exact_shape(tmp_path):
    client, llm = run_queries(["word"], tmp_path)

    resp = get_state(client)

    assert resp.status_code == 200
    assert llm.calls == 1
    cached_payload = json.dumps(
        {"canonical": WORD_FIXTURE["canonical"], **WORD_FIXTURE["translation"]}
    )
    assert mask_latency(resp.json()) == {
        "queries": [
            {
                "id": 1,
                "created_at": gh.FIXED_NOW.isoformat(),
                "raw_text": gh.KIND_TEXTS["word"],
                "kind": "word",
                "source_app": "golden-app",
                "source_title": None,
                "source_url": None,
                "client": "golden-runner",
                "translation": WORD_FIXTURE["translation"],
                "latency_ms": 0,
                "mode": "instant",
            }
        ],
        "words": [
            {
                "id": 1,
                "canonical": "throughput",
                "first_query_id": 1,
                "created_at": gh.FIXED_NOW.isoformat(),
                "occurrences": 1,
                "last_seen_at": gh.FIXED_NOW.isoformat(),
                "status": "active",
                "blocked_reason": None,
            }
        ],
        "links": [{"query_id": 1, "word_id": 1}],
        "cache": [
            {
                "canonical": "__word_case__ throughput",
                "lang": "en->ru",
                "translation_json": cached_payload,
            }
        ],
    }


def test_state_without_token_returns_401(tmp_path):
    client, _llm, conn = gh.app_with_fixture("word", tmp_path)
    conn.close()

    resp = client.get("/v1/state")

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_state_after_word_sentence_phrase_kinds(tmp_path):
    client, llm = run_queries(["word", "sentence", "phrase"], tmp_path)

    resp = get_state(client)

    assert resp.status_code == 200
    assert llm.calls == 3
    body = mask_latency(resp.json())

    assert [q["kind"] for q in body["queries"]] == ["word", "sentence", "phrase"]
    assert [q["translation"] for q in body["queries"]] == [
        WORD_FIXTURE["translation"],
        SENTENCE_FIXTURE["translation"],
        PHRASE_FIXTURE["translation"],
    ]
    # A word entry exists only for word/phrase queries; sentence creates none.
    assert [w["canonical"] for w in body["words"]] == ["throughput", "to be about to"]
    assert [
        (w["id"], w["first_query_id"], w["occurrences"]) for w in body["words"]
    ] == [
        (1, 1, 1),
        (2, 3, 1),
    ]
    assert body["links"] == [
        {"query_id": 1, "word_id": 1},
        {"query_id": 3, "word_id": 2},
    ]
    # Only word/phrase queries fill the cache; rows are sorted by canonical.
    assert [(r["canonical"], r["lang"]) for r in body["cache"]] == [
        ("__phrase_case__ to be about to", "en->ru"),
        ("__word_case__ throughput", "en->ru"),
    ]
    for row in body["cache"]:
        payload = json.loads(row["translation_json"])
        assert payload["canonical"] in ("to be about to", "throughput")


def test_snapshot_preserves_callers_row_factory(tmp_path):
    """snapshot() reads through a cursor-local row factory: the connection
    handed to it keeps its own row_factory untouched (and the snapshot is
    identical to what a sqlite3.Row connection yields)."""
    from app.service import state

    run_queries(["word"], tmp_path)
    conn = gh.db.connect(str(tmp_path / "golden.db"))
    conn.row_factory = None
    snap = state.snapshot(conn)
    conn.close()

    # The caller's factory survives the call...
    assert conn.row_factory is None
    # ...and the rows still read as dictionaries (cursor-local factory).
    assert [q["kind"] for q in snap["queries"]] == ["word"]
    assert [(w["canonical"], w["occurrences"]) for w in snap["words"]] == [
        ("throughput", 1)
    ]
