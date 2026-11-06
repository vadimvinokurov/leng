"""Release mirror: the server keeps a local copy of its own release.

`sync_mirror` is the pure logic (transport is injected, so tests feed it
fakes); `HttpxTransport` is the only production transport. The cache
lives next to the database (`/data/clients` in the container), never
inside the image.
"""

import json
import logging
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import Config

logger = logging.getLogger(__name__)

MANIFEST_NAME = "latest.json"


class Transport(Protocol):
    def fetch(self, url: str) -> bytes: ...


class TransportError(Exception):
    """The transport failed to fetch a URL."""


class MirrorError(Exception):
    """A mirror sync failed; the caller decides how loudly to complain."""


def clients_dir(cfg: Config) -> Path:
    return Path(cfg.db_path).parent / "clients"


def latest_json_url(cfg: Config) -> str:
    return (
        f"https://github.com/{cfg.github_repo}"
        f"/releases/download/v{cfg.version}/{MANIFEST_NAME}"
    )


class HttpxTransport:
    """Production transport over httpx; any non-2xx is a TransportError."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(follow_redirects=True, transport=transport)

    def fetch(self, url: str) -> bytes:
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            raise TransportError(f"{url}: {exc}") from exc
        if not resp.is_success:
            raise TransportError(f"{url}: HTTP {resp.status_code}")
        return resp.content


def sync_mirror(cfg: Config, transport: Transport) -> None:
    """Mirror the release tagged v<cfg.version> into clients_dir.

    A cached manifest is reused only when it names the running server's
    version; anything else (a previous release's manifest, a corrupt
    file) is discarded and re-downloaded — together with the artifacts,
    even when their names repeat across releases (final review: the
    stale-cache bug of 2026-11-06). Existing files of the CURRENT
    release are never re-downloaded. The manifest is downloaded and
    parsed BEFORE anything is written, and every file lands through an
    atomic temp-file + replace, so a crash or a failed download can
    never leave a truncated or corrupt file in the cache — sync skips
    existing files, which would keep serving it. The manifest is written
    last: only a fully mirrored release is advertised to clients. Any
    failure raises MirrorError; the caller (startup task or
    /v1/clients/refresh) decides how to report it.
    """
    target = clients_dir(cfg)
    target.mkdir(parents=True, exist_ok=True)

    manifest_path = target / MANIFEST_NAME
    cached: bytes | None = None
    fresh = True
    if manifest_path.exists():
        cached = manifest_path.read_bytes()
        try:
            fresh = _manifest_version(cached) != cfg.version
        except MirrorError:
            cached = None  # unparseable cache: treat as absent
        else:
            if not fresh:
                _platform_urls(cached)  # validate the cache shape

    if cached is None or fresh:
        manifest = _fetch(transport, latest_json_url(cfg))
        _platform_urls(manifest)
    else:
        manifest = cached

    for url in _platform_urls(manifest):
        name = urlparse(url).path.rsplit("/", 1)[-1]
        dest = target / name
        if dest.exists() and not fresh:
            continue
        content = _fetch(transport, url)
        _write(dest, content)

    if fresh:
        _write(manifest_path, manifest)


def _manifest_version(manifest: bytes) -> str | None:
    """The `version` field of a manifest; malformed manifests raise."""
    try:
        parsed = json.loads(manifest)
        version = parsed["version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise MirrorError(f"malformed updater manifest: {exc}") from exc
    if not isinstance(version, str):
        raise MirrorError(f"malformed updater manifest: version {version!r}")
    return version


def _fetch(transport: Transport, url: str) -> bytes:
    try:
        return transport.fetch(url)
    except TransportError as exc:
        raise MirrorError(f"mirror download failed: {exc}") from exc


def _platform_urls(manifest: bytes) -> list[str]:
    try:
        parsed = json.loads(manifest)
        urls = [
            entry["url"]
            for entry in parsed["platforms"].values()
            if isinstance(entry, dict) and "url" in entry
        ]
    except (ValueError, KeyError) as exc:
        raise MirrorError(f"malformed updater manifest: {exc}") from exc
    if not isinstance(parsed, dict) or "platforms" not in parsed:
        raise MirrorError("malformed updater manifest: no platforms map")
    return urls


def _write(path: Path, content: bytes) -> bytes:
    """Persist content atomically: temp file in the same dir, then
    replace, so readers never see a half-written file."""
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_bytes(content)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return content
