"""The downloads surface: page, updater manifest rewrite, file serving,
refresh. All public except refresh (token + version gate)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import mirror
from app.api.routes import create_app
from app.config import Config

TOKEN = "test-token"


@pytest.fixture
def release_cfg(tmp_path):
    return Config(
        api_token=TOKEN,
        db_path=str(tmp_path / "leng.db"),
        llm_base_url="http://localhost:0",
        llm_model="m",
        llm_api_key="k",
        llm_timeout_s=1.0,
        version="1.2.0",
    )


def platform_url(cfg: Config, filename: str) -> str:
    return f"https://github.com/{cfg.github_repo}/releases/download/v1.2.0/{filename}"


def manifest_bytes(cfg: Config) -> bytes:
    return json.dumps(
        {
            "version": "1.2.0",
            "pub_date": "2026-11-05T00:00:00Z",
            "platforms": {
                "macos": {
                    "signature": "sig",
                    "url": platform_url(cfg, "Leng.app.tar.gz"),
                },
                "windows-x86_64": {
                    "signature": "sig",
                    "url": platform_url(cfg, "Leng_1.2.0_x64-setup.exe"),
                },
            },
        }
    ).encode()


@pytest.fixture
def cached_cfg(release_cfg, tmp_path):
    """A release cfg whose mirror cache is already filled."""
    clients = Path(release_cfg.db_path).parent / "clients"
    clients.mkdir(parents=True)
    (clients / "latest.json").write_bytes(manifest_bytes(release_cfg))
    (clients / "Leng.app.tar.gz").write_bytes(b"mac-blob")
    (clients / "Leng_1.2.0_x64-setup.exe").write_bytes(b"win-blob")
    return release_cfg


@pytest.fixture
def serving_transport(release_cfg):
    """A fake GitHub transport serving this release's manifest and files."""

    class FakeTransport:
        def fetch(self, url: str) -> bytes:
            if url == mirror.latest_json_url(release_cfg):
                return manifest_bytes(release_cfg)
            name = url.rsplit("/", 1)[-1]
            if name == "Leng.app.tar.gz":
                return b"mac-blob"
            if name == "Leng_1.2.0_x64-setup.exe":
                return b"win-blob"
            raise mirror.TransportError(url)

    return FakeTransport()


def client_for(cfg, transport=None):
    from app.llm.fake import FakeLLM

    app = create_app(cfg, FakeLLM({})) if transport is None else create_app(
        cfg, FakeLLM({}), mirror_transport=transport
    )
    return TestClient(app)


def test_page_lists_cached_files(cached_cfg):
    resp = client_for(cached_cfg).get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "Leng 1.2.0" in body
    assert 'href="/v1/clients/files/Leng.app.tar.gz"' in body
    assert 'href="/v1/clients/files/Leng_1.2.0_x64-setup.exe"' in body
    assert "latest.json" not in body


def test_page_without_cache_links_github(release_cfg):
    resp = client_for(release_cfg).get("/")

    assert resp.status_code == 200
    assert (
        f"https://github.com/{release_cfg.github_repo}/releases" in resp.text
    )


def test_manifest_urls_point_to_this_server(cached_cfg):
    resp = client_for(cached_cfg).get("/v1/clients/latest.json")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["platforms"]) == {"macos", "windows-x86_64"}
    for entry in body["platforms"].values():
        assert entry["url"].startswith("http://testserver")
        assert entry["url"].endswith(
            entry["url"].rsplit("/", 1)[-1]
        ), entry["url"]
        assert "/v1/clients/files/" in entry["url"]


def test_manifest_without_cache_is_404(release_cfg):
    resp = client_for(release_cfg).get("/v1/clients/latest.json")

    assert resp.status_code == 404
    assert resp.json() == {"error": "mirror_empty"}


def test_file_serves_bytes(cached_cfg):
    resp = client_for(cached_cfg).get("/v1/clients/files/Leng.app.tar.gz")

    assert resp.status_code == 200
    assert resp.content == b"mac-blob"


def test_file_missing_is_404(cached_cfg):
    resp = client_for(cached_cfg).get("/v1/clients/files/not-there.exe")

    assert resp.status_code == 404
    assert resp.json() == {"error": "mirror_empty"}


def test_file_traversal_rejected(cached_cfg):
    client = client_for(cached_cfg)

    # ".." itself is normalized away client-side (httpx collapses the
    # segment), so the decodable equivalent is the honest probe.
    for name in ["..%2Flatest.json", "sub%2Fsecret.txt", "..%2E"]:
        resp = client.get(f"/v1/clients/files/{name}")
        assert resp.status_code == 404, name
        assert resp.json() == {"error": "mirror_empty"}, name


def test_refresh_syncs_and_reports(release_cfg, serving_transport):
    client = client_for(release_cfg, transport=serving_transport)
    headers = {"X-Api-Token": TOKEN, "X-Client-Version": "1.2.0"}

    resp = client.post("/v1/clients/refresh", json={}, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    clients = mirror.clients_dir(release_cfg)
    assert (clients / "Leng.app.tar.gz").read_bytes() == b"mac-blob"


def test_refresh_failure_is_502(release_cfg):
    class DeadTransport:
        def fetch(self, url: str) -> bytes:
            raise mirror.TransportError(url)

    client = client_for(release_cfg, transport=DeadTransport())
    headers = {"X-Api-Token": TOKEN, "X-Client-Version": "1.2.0"}

    resp = client.post("/v1/clients/refresh", json={}, headers=headers)

    assert resp.status_code == 502
    assert resp.json() == {"error": "mirror_unavailable"}


def test_refresh_requires_token(cached_cfg):
    resp = client_for(cached_cfg).post("/v1/clients/refresh", json={})

    assert resp.status_code == 401
