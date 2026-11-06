"""Release mirror: pure sync logic with an injected transport, plus the
startup wiring (release version syncs; dev skips; failures never break
the server)."""

import json

import pytest
from fastapi.testclient import TestClient

from app import mirror
from app.api.routes import create_app
from app.config import Config


def make_cfg(tmp_path, version="1.2.0"):
    return Config(
        api_token="t",
        db_path=str(tmp_path / "leng.db"),
        llm_base_url="http://localhost:0",
        llm_model="m",
        llm_api_key="k",
        llm_timeout_s=1.0,
        version=version,
    )


class FakeTransport:
    """Serves urls from a dict; everything else raises TransportError."""

    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        if url not in self.pages:
            raise mirror.TransportError(url)
        return self.pages[url]


class RaisingTransport:
    def fetch(self, url: str) -> bytes:
        raise mirror.TransportError(url)


def manifest_bytes(cfg: Config) -> bytes:
    base = f"https://github.com/{cfg.github_repo}"
    import json

    return json.dumps(
        {
            "version": "1.2.0",
            "pub_date": "2026-11-05T00:00:00Z",
            "platforms": {
                "macos": {
                    "signature": "sig",
                    "url": f"{base}/releases/download/v1.2.0/Leng.app.tar.gz",
                },
                "windows-x86_64": {
                    "signature": "sig",
                    "url": f"{base}/releases/download/v1.2.0/Leng_1.2.0_x64-setup.exe",
                },
            },
        }
    ).encode()


@pytest.fixture
def release_cfg(tmp_path):
    return make_cfg(tmp_path)


def test_latest_json_url_points_at_release_tag(release_cfg):
    assert mirror.latest_json_url(release_cfg) == (
        "https://github.com/vadimvinokurov/leng"
        "/releases/download/v1.2.0/latest.json"
    )


def test_sync_downloads_manifest_and_assets(release_cfg):
    base = f"https://github.com/{release_cfg.github_repo}"
    transport = FakeTransport(
        {
            f"{base}/releases/download/v1.2.0/latest.json": manifest_bytes(
                release_cfg
            ),
            f"{base}/releases/download/v1.2.0/Leng.app.tar.gz": b"mac-blob",
            f"{base}/releases/download/v1.2.0/Leng_1.2.0_x64-setup.exe": b"win-blob",
        }
    )

    mirror.sync_mirror(release_cfg, transport)

    clients = mirror.clients_dir(release_cfg)
    assert (clients / "latest.json").exists()
    assert (clients / "Leng.app.tar.gz").read_bytes() == b"mac-blob"
    assert (clients / "Leng_1.2.0_x64-setup.exe").read_bytes() == b"win-blob"


def test_sync_is_idempotent(release_cfg):
    # Seed the cache directly, then prove a fully-raising transport is
    # never touched: existing files are skipped without a fetch.
    clients = mirror.clients_dir(release_cfg)
    clients.mkdir(parents=True)
    (clients / "latest.json").write_bytes(manifest_bytes(release_cfg))
    (clients / "Leng.app.tar.gz").write_bytes(b"mac-blob")
    (clients / "Leng_1.2.0_x64-setup.exe").write_bytes(b"win-blob")

    spy = FakeTransport({})
    mirror.sync_mirror(release_cfg, spy)
    assert spy.fetched == []


def test_failed_download_raises(release_cfg):
    base = f"https://github.com/{release_cfg.github_repo}"
    transport = FakeTransport(
        {
            f"{base}/releases/download/v1.2.0/latest.json": manifest_bytes(
                release_cfg
            )
        }
    )

    with pytest.raises(mirror.MirrorError):
        mirror.sync_mirror(release_cfg, transport)


def test_httpx_transport_maps_get_to_bytes():
    """The production transport: 200 → body, anything else → TransportError."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, content=b"hello")
            if request.url.path == "/ok"
            else httpx.Response(404)
        )

    transport = mirror.HttpxTransport(httpx.MockTransport(handler))

    assert transport.fetch("https://example.com/ok") == b"hello"
    with pytest.raises(mirror.TransportError):
        transport.fetch("https://example.com/missing")


def test_startup_sync_runs_on_release_version(release_cfg, tmp_path):
    base = f"https://github.com/{release_cfg.github_repo}"
    spy = FakeTransport(
        {
            mirror.latest_json_url(release_cfg): manifest_bytes(release_cfg),
            f"{base}/releases/download/v1.2.0/Leng.app.tar.gz": b"mac",
            f"{base}/releases/download/v1.2.0/Leng_1.2.0_x64-setup.exe": b"win",
        }
    )
    from app.llm.fake import FakeLLM

    with TestClient(create_app(release_cfg, FakeLLM({}), mirror_transport=spy)):
        assert mirror.latest_json_url(release_cfg) in spy.fetched


def test_failed_sync_does_not_break_startup(release_cfg):
    from app.llm.fake import FakeLLM

    with TestClient(
        create_app(release_cfg, FakeLLM({}), mirror_transport=RaisingTransport())
    ) as client:
        resp = client.get("/v1/health")
        assert resp.status_code == 200


def test_dev_version_skips_sync(tmp_path):
    dev_cfg = make_cfg(tmp_path, version="dev")
    spy = RaisingTransport()
    spy.calls = 0  # type: ignore[attr-defined]

    def counting_fetch(url: str) -> bytes:
        spy.calls += 1  # type: ignore[attr-defined]
        raise mirror.TransportError(url)

    spy.fetch = counting_fetch  # type: ignore[method-assign]
    from app.llm.fake import FakeLLM

    with TestClient(create_app(dev_cfg, FakeLLM({}), mirror_transport=spy)):
        assert spy.calls == 0  # type: ignore[attr-defined]


def test_failed_download_leaves_no_partial_file(release_cfg):
    """A download that dies mid-way must not leave a truncated artifact in
    the cache: sync skips existing files, so a partial file would be
    served forever (final review: Important 1)."""
    base = f"https://github.com/{release_cfg.github_repo}"
    transport = FakeTransport(
        {
            f"{base}/releases/download/v1.2.0/latest.json": manifest_bytes(
                release_cfg
            ),
            f"{base}/releases/download/v1.2.0/Leng.app.tar.gz": b"mac-blob",
            # The Windows artifact never arrives (connection dies).
        }
    )

    with pytest.raises(mirror.MirrorError):
        mirror.sync_mirror(release_cfg, transport)

    clients = mirror.clients_dir(release_cfg)
    assert (clients / "Leng.app.tar.gz").read_bytes() == b"mac-blob"
    assert not (clients / "Leng_1.2.0_x64-setup.exe").exists(), (
        "a failed download must not leave a partial file"
    )


def test_invalid_manifest_cache_is_not_written(release_cfg):
    """A manifest that does not parse must reach neither the cache nor
    the disk (final review: Important 1): the page and the manifest
    endpoint keep working instead of serving a corrupt cache."""
    base = f"https://github.com/{release_cfg.github_repo}"
    transport = FakeTransport(
        {f"{base}/releases/download/v1.2.0/latest.json": b"not json at all"}
    )

    with pytest.raises(mirror.MirrorError):
        mirror.sync_mirror(release_cfg, transport)

    clients = mirror.clients_dir(release_cfg)
    assert not (clients / "latest.json").exists()


def test_upgraded_server_refreshes_stale_manifest(release_cfg, tmp_path):
    """The real upgrade path: the server moves from one release version
    to the next, the cache already holds the previous manifest. A cache
    tied to a different version than the running server must be fully
    refreshed — including files whose names repeat across releases."""
    base = f"https://github.com/{release_cfg.github_repo}"
    transport = FakeTransport(
        {
            f"{base}/releases/download/v1.2.0/latest.json": manifest_bytes(
                release_cfg
            ),
            f"{base}/releases/download/v1.2.0/Leng.app.tar.gz": b"mac-blob-v1",
            f"{base}/releases/download/v1.2.0/Leng_1.2.0_x64-setup.exe": b"win-blob-v1",
        }
    )
    mirror.sync_mirror(release_cfg, transport)

    new_cfg = make_cfg(tmp_path, version="1.3.0")
    new_base = f"https://github.com/{new_cfg.github_repo}"
    new_manifest = json.dumps(
        {
            "version": "1.3.0",
            "pub_date": "2026-11-06T00:00:00Z",
            "platforms": {
                "macos": {
                    "signature": "sig",
                    "url": f"{new_base}/releases/download/v1.3.0/Leng.app.tar.gz",
                },
            },
        }
    ).encode()
    upgrade_transport = FakeTransport(
        {
            f"{new_base}/releases/download/v1.3.0/latest.json": new_manifest,
            f"{new_base}/releases/download/v1.3.0/Leng.app.tar.gz": b"mac-blob-v2",
        }
    )

    mirror.sync_mirror(new_cfg, upgrade_transport)

    clients = mirror.clients_dir(new_cfg)
    assert json.loads((clients / "latest.json").read_bytes())["version"] == "1.3.0"
    assert (clients / "Leng.app.tar.gz").read_bytes() == b"mac-blob-v2", (
        "a same-named artifact of the old release must be overwritten"
    )


def test_malformed_cached_manifest_is_refreshed(release_cfg, tmp_path):
    """A corrupt cache must not wedge the mirror forever: an
    unparseable cached manifest is thrown away and re-downloaded."""
    base = f"https://github.com/{release_cfg.github_repo}"
    clients = mirror.clients_dir(release_cfg)
    clients.mkdir(parents=True)
    (clients / "latest.json").write_bytes(b"garbage not json")
    (clients / "Leng.app.tar.gz").write_bytes(b"stale-blob")

    transport = FakeTransport(
        {
            f"{base}/releases/download/v1.2.0/latest.json": manifest_bytes(
                release_cfg
            ),
            f"{base}/releases/download/v1.2.0/Leng.app.tar.gz": b"mac-blob",
            f"{base}/releases/download/v1.2.0/Leng_1.2.0_x64-setup.exe": b"win-blob",
        }
    )

    mirror.sync_mirror(release_cfg, transport)

    assert json.loads((clients / "latest.json").read_bytes())["version"] == "1.2.0"
    assert (clients / "Leng.app.tar.gz").read_bytes() == b"mac-blob"
