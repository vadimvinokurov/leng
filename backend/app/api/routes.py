"""FastAPI app factory: /v1 routes generated from the command registry.

`create_app` is a generic creation loop over `app.commands.registry.COMMANDS`:
each command contributes one route through its builder, with token
authentication attached when the command requires it.
"""

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import mirror
from app.commands import registry
from app.commands.registry import AppContext, CommandError
from app.config import Config
from app.db import apply_migrations, connect
from app.llm.base import LLMClient

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    cfg: Config,
    llm: LLMClient,
    clock: Callable[[], datetime] = _utcnow,
    mirror_transport: mirror.Transport | None = None,
) -> FastAPI:
    """Wire the database and an LLM client into the API application.

    `clock` supplies the current time to each query and defaults to the
    real UTC clock; tests inject a fixed clock for deterministic
    timestamps.

    `mirror_transport` replaces the production GitHub transport in tests.
    A release-version server mirrors its own release at startup — inline
    when a transport was injected (deterministic tests), in a daemon
    thread otherwise (startup never waits on the network).
    """
    bootstrap = connect(cfg.db_path)
    apply_migrations(bootstrap)
    bootstrap.close()

    ctx = AppContext(cfg=cfg, llm=llm, clock=clock, mirror_transport=mirror_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if cfg.version != "dev":

            def run_sync() -> None:
                try:
                    mirror.sync_mirror(
                        cfg, ctx.mirror_transport or mirror.HttpxTransport()
                    )
                except (mirror.MirrorError, mirror.TransportError) as exc:
                    logger.warning("release mirror sync failed: %s", exc)

            if ctx.mirror_transport is not None:
                run_sync()
            else:
                threading.Thread(target=run_sync, daemon=True).start()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Render every HTTP error with the flat {"error": <code>} body.

        Starlette's router raises its own HTTPException (404 for unknown
        paths) which fastapi.HTTPException handlers do not cover, so the
        base class is handled here instead.
        """
        content = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(CommandError)
    def command_error(_: Request, exc: CommandError) -> JSONResponse:
        """Render registry CommandError with the same flat {"error": <code>} body."""
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    def require_token(x_api_token: str | None = Header(default=None)) -> None:
        if x_api_token != cfg.api_token:
            raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    def require_version(x_client_version: str | None = Header(default=None)) -> None:
        """Authed clients must speak the server's release version.

        A "dev" build skips the check entirely: that is the local
        development mode, not an API surface worth protecting.
        """
        if cfg.version != "dev" and x_client_version != cfg.version:
            raise HTTPException(status_code=409, detail={"error": "version_mismatch"})

    def _endpoint_for(command: registry.Command) -> Callable[..., Any]:
        """Turn the builder's endpoint into a FastAPI-callable.

        JSON commands pass through; the updater manifest gets its
        platform URLs rewritten to this server so client updates never
        leave the mirror. html/file renders keep their original
        signatures and are rendered by `response_class` below.
        """
        raw = command.builder(ctx)
        if command.id != "client_manifest":
            return raw

        def manifest(request: Request) -> dict[str, Any]:
            parsed: dict[str, Any] = raw()
            base = str(request.base_url).rstrip("/")
            for entry in parsed.get("platforms", {}).values():
                if isinstance(entry, dict) and "url" in entry:
                    name = urlparse(entry["url"]).path.rsplit("/", 1)[-1]
                    entry["url"] = f"{base}/v1/clients/files/{name}"
            return parsed

        return manifest

    # Rendering for non-JSON commands happens through response_class:
    # the builder's return value (html str / file path) is passed to the
    # response class unchanged, and FastAPI still sees the real
    # signature for parameter injection.
    RESPONSE_CLASSES = {
        "html": HTMLResponse,
        "file": FileResponse,
    }

    for command in registry.COMMANDS:
        if command.method == "POST" and command.params is None:
            raise ValueError(
                f"command {command.id!r}: POST requires a params model"
            )
        response_class = RESPONSE_CLASSES.get(command.renders, JSONResponse)
        app.add_api_route(
            command.path,
            _endpoint_for(command),
            methods=[command.method],
            response_class=response_class,
            dependencies=(
                [Depends(require_token), Depends(require_version)]
                if command.auth
                else []
            ),
        )

    return app
