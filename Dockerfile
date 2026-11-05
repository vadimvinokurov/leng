# Leng backend image. `uv` is copied from the official image; `uv sync`
# installs the locked deps into .venv, and at runtime uvicorn is run
# from that venv's bin -- uv itself is not needed after the build.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app/backend
COPY backend/ /app/backend/
RUN uv sync --frozen --no-dev --python-preference only-system

ENV PATH="/app/backend/.venv/bin:$PATH"

EXPOSE 8000

ENTRYPOINT ["/app/backend/.venv/bin/uvicorn"]
CMD ["app.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
