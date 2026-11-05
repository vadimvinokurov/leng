"""OpenAI-compatible HTTP LLM client with a one-shot JSON retry."""

import json
from typing import Any

import httpx

from app.config import Config
from app.llm.base import LLMError

JSON_RETRY_SUFFIX = "Return only valid JSON per schema"


class OpenAICompatLLM:
    """LLMClient speaking the OpenAI chat completions protocol."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = httpx.Client(timeout=cfg.llm_timeout_s)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self._complete(system, user)
        parsed = self._parse(text)
        if parsed is not None:
            return parsed
        retry_text = self._complete(f"{system}\n{JSON_RETRY_SUFFIX}", user)
        retry_parsed = self._parse(retry_text)
        if retry_parsed is None:
            raise LLMError(f"model returned invalid JSON twice: {retry_text!r}")
        return retry_parsed

    def _complete(self, system: str, user: str) -> str:
        """POST one completion and return choices[0].message.content.

        Any HTTP-layer failure (including a timeout) raises LLMError.
        """
        try:
            response = self._client.post(
                f"{self._cfg.llm_base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._cfg.llm_api_key}"},
                json={
                    "model": self._cfg.llm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError("LLM completion request failed") from exc
        if response.is_error:
            raise LLMError(
                f"LLM completion failed: HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            text = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"LLM completion returned malformed body "
                f"(HTTP {response.status_code}): {response.text[:200]}"
            ) from exc
        if not isinstance(text, str):
            raise LLMError("completion response has no text content")
        return text

    @staticmethod
    def _parse(text: str) -> dict[str, Any] | None:
        """Parsed dict for valid JSON objects, None for anything else."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
