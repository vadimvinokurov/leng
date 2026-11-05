"""Prompt file loader: prompts live in files, never as inline strings."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load(name: str) -> str:
    """Return the raw text of backend/prompts/{name}.md.

    The text is returned unformatted; the caller applies
    ``.format(text=...)`` to substitute the query text into the ``{text}``
    placeholder. Resolution is relative to the app package location, so the
    result does not depend on the current working directory.
    """
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
