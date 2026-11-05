"""Pydantic request models for the HTTP API."""

from pydantic import BaseModel


class Source(BaseModel):
    app: str
    title: str | None = None
    url: str | None = None


class QueryIn(BaseModel):
    raw_text: str
    source: Source
    client: str
