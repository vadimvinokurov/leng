"""HTTP-level tests for the containerized fake LLM server handler."""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
from fake_llm_server import load_fixtures, make_handler

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "llm"


def completion_request(user_text: str) -> dict:
    return {
        "model": "glm5.3-flash:claude",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": user_text},
        ],
    }


def post(client, server, payload):
    return client.post(
        f"http://{server.server_address[0]}:{server.server_address[1]}"
        f"/v1/chat/completions",
        json=payload,
    )


def with_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def make_client():
    return httpx.Client(timeout=5.0)


def test_plain_user_text_serves_the_word_fixture():
    fixtures = load_fixtures(FIXTURES_DIR)
    server = with_server(make_handler(fixtures))
    with make_client() as client, server:
        response = post(client, server, completion_request("throughput"))
    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content == fixtures["word"]
    assert content["kind"] == "word"
    response.raise_for_status()


def test_sentence_marker_serves_the_sentence_fixture():
    fixtures = load_fixtures(FIXTURES_DIR)
    server = with_server(make_handler(fixtures))
    with make_client() as client, server:
        response = post(
            client, server, completion_request("He stood __SENTENCE_CASE__ still.")
        )
    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content == fixtures["sentence"]
    assert content["kind"] == "sentence"


def test_unknown_path_is_404():
    fixtures = load_fixtures(FIXTURES_DIR)
    server = with_server(make_handler(fixtures))
    with make_client() as client, server:
        response = client.post(
            f"http://{server.server_address[0]}:{server.server_address[1]}/v1/other",
            json=completion_request("throughput"),
        )
    assert response.status_code == 404
