"""Tests for GET /v1/commands: the machine-readable command listing."""


import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_app
from app.config import Config

API_TOKEN = "test-token"


@pytest.fixture
def client(tmp_path) -> TestClient:
    cfg = Config(
        api_token=API_TOKEN,
        db_path=str(tmp_path / "commands.db"),
        llm_base_url="http://localhost:0",
        llm_model="test-model",
        llm_api_key="test-key",
        llm_timeout_s=1.0,
    )
    from app.llm.fake import FakeLLM

    return TestClient(create_app(cfg, FakeLLM({})))


def test_commands_endpoint_is_public_and_lists_all_commands(client):
    resp = client.get("/v1/commands")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"commands"}
    assert [c["id"] for c in body["commands"]] == [
        "health",
        "downloads_page",
        "client_manifest",
        "client_file",
        "client_refresh",
        "query",
        "word_history",
        "commands",
        "state",
    ]


def test_commands_entries_carry_exact_keys(client):
    body = client.get("/v1/commands").json()

    for entry in body["commands"]:
        assert set(entry) == {"id", "method", "path", "auth", "params_schema"}


def test_commands_health_has_no_auth_and_no_params_schema(client):
    body = client.get("/v1/commands").json()

    entry = next(c for c in body["commands"] if c["id"] == "health")
    assert entry == {
        "id": "health",
        "method": "GET",
        "path": "/v1/health",
        "auth": False,
        "params_schema": None,
    }


def test_commands_query_exposes_params_schema_with_raw_text(client):
    body = client.get("/v1/commands").json()

    entry = next(c for c in body["commands"] if c["id"] == "query")
    assert entry["auth"] is True
    assert entry["method"] == "POST"
    assert entry["path"] == "/v1/query"
    assert entry["params_schema"] is not None
    assert "raw_text" in entry["params_schema"]["properties"]
