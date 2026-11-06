"""Translator persona: per-user personalization loaded from a data file.

The persona file (by default /data/persona.md, override with
LENG_PERSONA_PATH) carries a de-identified user profile — domain and a
preferred-renderings list — that process_query appends to the
translation system prompt. Read on every request so edits to the file
apply without a restart; a missing or unreadable file means no
personalization.
"""

import os
from pathlib import Path

PERSONA_PATH_ENV = "LENG_PERSONA_PATH"
DEFAULT_PERSONA_PATH = "/data/persona.md"


def load_persona() -> str:
    """Return the persona text, or "" when there is none."""
    path = Path(os.environ.get(PERSONA_PATH_ENV, DEFAULT_PERSONA_PATH))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
