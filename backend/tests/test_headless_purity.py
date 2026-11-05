"""Headless purity guard on the service core (rule 1).

Parses every core module's source with `ast` — the target modules are
never imported, so the guard cannot be fooled by import-time side
effects and stays headless itself. Each core file's top-level imports
are collected and checked against the forbidden set of UI/server
frameworks; `pydantic`, `httpx` and the standard library stay allowed.
A violation fails with the full list of `file:module` offenders.
"""

import ast
from pathlib import Path

FORBIDDEN_ROOT_MODULES = {
    "fastapi",
    "uvicorn",
    "tkinter",
    "PyQt6",
    "PySide6",
    "flask",
    "turtle",
}

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Core module paths that must stay free of UI/server imports.
CORE_FILES = [
    APP_DIR / "db.py",
    *(APP_DIR / "service").glob("*.py"),
    *(APP_DIR / "llm").glob("*.py"),
    *(APP_DIR / "commands").glob("*.py"),
    (APP_DIR / "prompts" / "__init__.py"),
]


def _top_level_import_names(path: Path) -> list[str]:
    """Return the root package of every top-level import in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.module else []
        else:
            continue
        roots.extend(name.split(".")[0] for name in names if name)
    return roots


def test_core_modules_import_no_ui_frameworks():
    violations = []
    for path in CORE_FILES:
        for root in _top_level_import_names(path):
            if root in FORBIDDEN_ROOT_MODULES:
                violations.append(f"{path.relative_to(APP_DIR)}: {root}")

    assert violations == [], (
        "headless purity violated: forbidden imports in core modules:\n"
        + "\n".join(sorted(violations))
    )
