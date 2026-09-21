"""Prompts live in `prompts.yaml`, not in code, so they can be edited freely.

The YAML holds plain-text templates with `$placeholders` (Python
`string.Template` syntax — chosen because `{{c1::...}}` cloze markup in the
prompt text must survive untouched). Templates are checked when loaded: an
unknown placeholder (a typo such as `$higlights`) or a stray `$` stops the
server at startup with the offending name, instead of reaching Claude as
literal text. A literal dollar sign is written `$$`.
"""
from __future__ import annotations

from pathlib import Path
from string import Template

import yaml

KINDS = ("cards", "zettel")
SHARED_KEYS = ("untrusted_note", "comment_rule")
PLACEHOLDERS = frozenset(
    ("title", "url", "page_text", "highlights", "vocabulary", *SHARED_KEYS))


class PromptsError(Exception):
    """prompts.yaml is missing or malformed; the message says what to fix."""


def _template(kind: str, text: str) -> Template:
    tpl = Template(text.strip())
    if not tpl.is_valid():
        raise PromptsError(
            f"prompts.yaml: {kind}.prompt has a stray '$' (write '$$' for a literal one)")
    unknown = sorted(set(tpl.get_identifiers()) - PLACEHOLDERS)
    if unknown:
        raise PromptsError(
            f"prompts.yaml: {kind}.prompt uses unknown placeholder "
            f"${unknown[0]}; available: {', '.join('$' + p for p in sorted(PLACEHOLDERS))}")
    return tpl


def _max_tokens(kind: str, value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        raise PromptsError(
            f"prompts.yaml: {kind}.max_tokens must be a positive integer; got {value!r}")
    return n


class Prompts:
    def __init__(self, data: dict):
        shared = data.get("shared") or {}
        missing = [k for k in SHARED_KEYS if not shared.get(k)]
        if missing:
            raise PromptsError(f"prompts.yaml: shared.{missing[0]} is missing")
        self.shared = {k: str(shared[k]).strip() for k in SHARED_KEYS}
        self._templates: dict[str, Template] = {}
        self.max_tokens: dict[str, int] = {}
        for kind in KINDS:
            section = data.get(kind) or {}
            text = section.get("prompt")
            if not text or not str(text).strip():
                raise PromptsError(f"prompts.yaml: {kind}.prompt is missing")
            self._templates[kind] = _template(kind, str(text))
            self.max_tokens[kind] = _max_tokens(kind, section.get("max_tokens", 16000))

    def render(self, kind: str, *, title: str, url: str, page_text: str,
               highlights: str, vocabulary: str) -> str:
        return self._templates[kind].substitute(
            **self.shared, title=title, url=url, page_text=page_text,
            highlights=highlights, vocabulary=vocabulary)


def load_prompts(path: Path) -> Prompts:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PromptsError(f"prompts file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise PromptsError(f"prompts.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptsError("prompts.yaml must be a mapping with shared/cards/zettel keys")
    return Prompts(data)
