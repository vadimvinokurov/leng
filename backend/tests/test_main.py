"""Tests for the real entrypoint: Config + OpenAICompatLLM wiring."""

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Give the factory a complete, pointing-at-nothing environment."""
    monkeypatch.setenv("LENG_API_TOKEN", "t")
    monkeypatch.setenv("LENG_DB_PATH", str(tmp_path / "leng.db"))
    monkeypatch.setenv("LENG_LLM_BASE_URL", "http://llm.invalid")
    monkeypatch.setenv("LENG_LLM_API_KEY", "k")
    return tmp_path


def test_factory_builds_health_app_from_env(monkeypatch, env):
    app = main.app()
    assert TestClient(app).get("/v1/health").json() == {"status": "ok", "version": "dev"}


def test_factory_applies_migrations_on_bootstrap(env):
    main.app()
    assert (env / "leng.db").exists()


def test_factory_requires_mandatory_env(monkeypatch):
    for name in (
        "LENG_API_TOKEN",
        "LENG_DB_PATH",
        "LENG_LLM_BASE_URL",
        "LENG_LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(KeyError):
        main.app()
