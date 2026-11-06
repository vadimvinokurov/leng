"""Deterministic in-memory LLM client for tests and local end-to-end runs."""

from typing import Any


class FakeLLM:
    """Routes complete_json calls by a marker substring in the user prompt.

    responses maps a marker such as "__WORD_CASE__" to a response dict
    (typically loaded from backend/tests/fixtures/llm). Each call counts
    in `calls`.
    """

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls = 0
        self.last_system: str | None = None

    # `system` used to go unnoticed here (FakeLLM routes only on the user
    # prompt), but it is part of the LLMClient protocol (app.llm.base) and
    # is now recorded for tests that assert on the system prompt.
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls += 1
        self.last_system = system
        for marker, response in self._responses.items():
            if marker in user:
                return response
        raise KeyError(f"no response configured for user prompt: {user!r}")
