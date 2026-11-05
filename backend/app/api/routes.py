"""FastAPI app factory: /v1 routes generated from the command registry.

`create_app` is a generic creation loop over `app.commands.registry.COMMANDS`:
each command contributes one route through its builder, with token
authentication attached when the command requires it.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.commands import registry
from app.commands.registry import AppContext, CommandError
from app.config import Config
from app.db import apply_migrations, connect
from app.llm.base import LLMClient


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    cfg: Config, llm: LLMClient, clock: Callable[[], datetime] = _utcnow
) -> FastAPI:
    """Wire the database and an LLM client into the API application.

    `clock` supplies the current time to each query and defaults to the
    real UTC clock; tests inject a fixed clock for deterministic
    timestamps.
    """
    bootstrap = connect(cfg.db_path)
    apply_migrations(bootstrap)
    bootstrap.close()

    app = FastAPI()

    @app.exception_handler(HTTPException)
    def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        """Render every HTTP error with the flat {"error": <code>} body."""
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

    ctx = AppContext(cfg=cfg, llm=llm, clock=clock)

    for command in registry.COMMANDS:
        if command.method == "POST" and command.params is None:
            raise ValueError(
                f"command {command.id!r}: POST requires a params model"
            )
        app.add_api_route(
            command.path,
            command.builder(ctx),
            methods=[command.method],
            dependencies=(
                [Depends(require_token), Depends(require_version)]
                if command.auth
                else []
            ),
        )

    return app
