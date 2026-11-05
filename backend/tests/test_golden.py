"""Golden corpus: pinned /v1/query responses per kind.

Three guards live here: every kind replays byte-identically against its
pinned golden file, two fresh-DB runs are byte-identical to each other
(determinism), and the word case matches values computed by hand,
independently of any engine output.
"""

import json
from pathlib import Path

import golden_helpers as gh
import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"

KINDS = ["word", "phrase", "sentence", "paragraph", "fragment"]


def golden_bytes(obj) -> str:
    """The pinned on-disk JSON format: 2-space indent, UTF-8, one newline."""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def run_query(kind: str, tmp_path):
    """One full harness run for `kind`; returns (response, llm, done-conn)."""
    client, llm, conn = gh.app_with_fixture(kind, tmp_path)
    resp = client.post(
        "/v1/query", json=gh.query_body(gh.KIND_TEXTS[kind]), headers=gh.AUTH_HEADERS
    )
    conn.close()
    return resp, llm


@pytest.mark.parametrize("kind", KINDS)
def test_query_response_matches_golden(kind, tmp_path):
    resp, llm = run_query(kind, tmp_path)

    assert resp.status_code == 200
    assert llm.calls == 1
    golden_file = GOLDEN_DIR / f"query_{kind}.json"
    assert resp.json() == json.loads(golden_file.read_text(encoding="utf-8"))
    assert golden_bytes(resp.json()) == golden_file.read_text(encoding="utf-8")


def test_two_fresh_runs_are_byte_identical(tmp_path):
    bodies = []
    for i in range(2):
        resp, _ = run_query("word", tmp_path / f"run{i}")
        assert resp.status_code == 200
        bodies.append(golden_bytes(resp.json()))

    assert bodies[0] == bodies[1]


def test_engine_output_matches_hand_computed_word(tmp_path):
    resp, _ = run_query("word", tmp_path)

    assert resp.status_code == 200
    hand = json.loads(
        (GOLDEN_DIR / "golden_word_hand.json").read_text(encoding="utf-8")
    )
    assert resp.json()["kind"] == hand["kind"]
    assert resp.json()["canonical"] == hand["canonical"]
    assert resp.json()["response"] == hand["response"]


def test_state_snapshot_matches_golden(tmp_path):
    client, _, conn = gh.app_with_fixture("word", tmp_path)
    conn.close()
    resp = client.post(
        "/v1/query", json=gh.query_body(gh.KIND_TEXTS["word"]), headers=gh.AUTH_HEADERS
    )
    assert resp.status_code == 200

    state = client.get("/v1/state", headers=gh.AUTH_HEADERS)
    assert state.status_code == 200
    snapshot = state.json()
    for query in snapshot["queries"]:
        query["latency_ms"] = 0  # only nondeterministic field

    golden_file = GOLDEN_DIR / "state_after_single_word.json"
    assert golden_bytes(snapshot) == golden_file.read_text(encoding="utf-8")
