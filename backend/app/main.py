"""Application entrypoint for the uvicorn factory mode.

`uvicorn app.main:app --factory` calls the `app` factory below, which
is the only place wiring the real configuration and LLM client together
(Config from LENG_* environment, OpenAI-compatible HTTP client).
"""

from fastapi import FastAPI

from app.api.routes import create_app
from app.config import Config
from app.llm.openai_compat import OpenAICompatLLM


def app() -> FastAPI:
    """Build the real application from environment configuration."""
    cfg = Config.from_env()
    return create_app(cfg, OpenAICompatLLM(cfg))
