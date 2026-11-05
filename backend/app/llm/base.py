"""LLM response contract: pydantic models and the client Protocol.

The pinned response schema every MVP-1 LLM task must honor:

    {"kind": "word|phrase|sentence|paragraph|fragment",
     "canonical": "string or null (null for kind sentence|paragraph|fragment)",
     "translation": {"main": "translation",
                     "notes": ["optional remarks; [] for sentences"]}}
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, model_validator

Kind = Literal["word", "phrase", "sentence", "paragraph", "fragment"]


class LLMError(Exception):
    """Raised by LLM clients when a completion cannot be served as JSON."""


class LLMTranslation(BaseModel):
    main: str
    notes: list[str] = []


class LLMQueryResponse(BaseModel):
    kind: Kind
    canonical: str | None
    translation: LLMTranslation

    @model_validator(mode="after")
    def canonical_required_for_word_and_phrase(self) -> "LLMQueryResponse":
        if self.kind in ("word", "phrase") and self.canonical is None:
            raise ValueError("canonical is required for kind 'word' and 'phrase'")
        return self


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return the model's parsed JSON response for one completion call."""
