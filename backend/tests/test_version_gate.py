"""Version gate: authed commands reject clients of a different version.

Uses /v1/state as the probe: it is an auth command like /v1/query but
needs no LLM fixtures, and the gate is defined once for all auth routes.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import create_app
from app.config import Config

TOKEN = "test-token"


@pytest.fixture
def release_env(tmp_path):
    cfg = Config(
        api_token=TOKEN,
        db_path=str(tmp_path / "g.db"),
        llm_base_url="http://localhost:0",
        llm_model="m",
        llm_api_key="k",
        llm_timeout_s=1.0,
        version="0.2.0",
    )
    return cfg


@pytest.fixture
def dev_env(tmp_path):
    cfg = Config(
        api_token=TOKEN,
        db_path=str(tmp_path / "d.db"),
        llm_base_url="http://localhost:0",
        llm_model="m",
        llm_api_key="k",
        llm_timeout_s=1.0,
        version="dev",
    )
    return cfg


def client_for(cfg, responses=None):
    from app.llm.fake import FakeLLM

    return TestClient(create_app(cfg, FakeLLM(responses or {})))


def get_state(client, token=TOKEN, version=None):
    headers = {"X-Api-Token": token}
    if version is not None:
        headers["X-Client-Version"] = version
    return client.get("/v1/state", headers=headers)


def test_matching_version_allowed(release_env):
    resp = get_state(client_for(release_env), version="0.2.0")

    assert resp.status_code == 200


def test_missing_header_rejected(release_env):
    resp = get_state(client_for(release_env))

    assert resp.status_code == 409
    assert resp.json() == {"error": "version_mismatch"}


def test_wrong_version_rejected(release_env):
    resp = get_state(client_for(release_env), version="0.1.0")

    assert resp.status_code == 409
    assert resp.json() == {"error": "version_mismatch"}


def test_dev_server_accepts_any_version(dev_env):
    client = client_for(dev_env)

    assert get_state(client, version="9.9.9").status_code == 200
    assert get_state(client).status_code == 200


def test_token_wins_over_version(release_env):
    resp = get_state(client_for(release_env), token="wrong", version="0.1.0")

    assert resp.status_code == 401


def test_gate_covers_word_history(release_env, tmp_path):
    """The gate is per-auth-route, not a per-endpoint special case: another
    auth command (word_history) rejects the same way."""
    resp = client_for(release_env).get(
        "/v1/word/1/history", headers={"X-Api-Token": TOKEN}
    )

    assert resp.status_code == 409
