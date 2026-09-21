"""Uvicorn entrypoint: highlightanki.main:app

Configuration problems (no API key, broken prompts.yaml) stop the server
here, at startup, with a one-line explanation — never on the first request.
"""
from __future__ import annotations

import sys

from .app import create_app
from .config import ConfigError, load_settings
from .llm import LLM
from .prompts import PromptsError, load_prompts

try:
    settings = load_settings()
    prompts = load_prompts(settings.prompts_path)
except (ConfigError, PromptsError) as exc:
    sys.exit(f"highlight-anki-zettel: {exc}")

app = create_app(LLM(model=settings.model), settings, prompts)
