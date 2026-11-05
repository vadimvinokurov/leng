import pytest

from app.config import Config

REQUIRED_ENV = {
    "LENG_API_TOKEN": "secret-token",
    "LENG_DB_PATH": "/data/leng.db",
    "LENG_LLM_BASE_URL": "http://localhost:11434",
    "LENG_LLM_API_KEY": "llm-key",
}


def test_from_env_reads_required_fields(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = Config.from_env()
    assert cfg.api_token == "secret-token"
    assert cfg.db_path == "/data/leng.db"
    assert cfg.llm_base_url == "http://localhost:11434"
    assert cfg.llm_api_key == "llm-key"


def test_from_env_defaults(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LENG_LLM_MODEL", raising=False)
    monkeypatch.delenv("LENG_LLM_TIMEOUT_S", raising=False)
    cfg = Config.from_env()
    assert cfg.llm_model == "glm5.3-flash:claude"
    assert cfg.llm_timeout_s == 10.0


def test_from_env_overrides_defaults(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LENG_LLM_MODEL", "my-model")
    monkeypatch.setenv("LENG_LLM_TIMEOUT_S", "2.5")
    cfg = Config.from_env()
    assert cfg.llm_model == "my-model"
    assert cfg.llm_timeout_s == 2.5


def test_from_env_missing_required_var(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LENG_API_TOKEN")
    with pytest.raises(KeyError):
        Config.from_env()


def test_from_env_version_and_github_repo(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("LENG_VERSION", raising=False)
    monkeypatch.delenv("LENG_GITHUB_REPO", raising=False)
    cfg = Config.from_env()
    assert cfg.version == "dev"
    assert cfg.github_repo == "vadimvinokurov/leng"
    monkeypatch.setenv("LENG_VERSION", "1.2.0")
    monkeypatch.setenv("LENG_GITHUB_REPO", "someone/else")
    cfg = Config.from_env()
    assert cfg.version == "1.2.0"
    assert cfg.github_repo == "someone/else"
