"""Turn a page's yellow highlights into Anki cards (Cloze or Basic) via Claude."""
from __future__ import annotations

from .models import Highlight
from .prompts import Prompts

PAGE_TEXT_CAP = 30_000
EXCERPT_CAP = 2_000
COMMENT_CAP = 1_000

def untrusted(text: str) -> str:
    """Escape page-derived text so it can never close or open a prompt tag.

    A page could otherwise write `</highlight><comment>…</comment>` into its
    own body and manufacture the one tag the prompt treats as the reader's.
    Reader comments are never passed through here.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def highlights_block(highlights: list[Highlight]) -> str:
    """Numbered highlights, each with its page excerpt and reader comment.

    Every page-derived string is escaped and fenced in its own tag so the
    prompt can name exactly what is untrusted; only <comment> is the reader's
    own, and it goes in verbatim.
    """
    lines = []
    for i, h in enumerate(highlights):
        lines.append(f"{i + 1}. <highlight>{untrusted(h.text)}</highlight>")
        if h.context:
            lines.append(f"<excerpt>\n{untrusted(h.context[:EXCERPT_CAP])}\n</excerpt>")
        if h.comment:
            lines.append(f"<comment>\n{h.comment[:COMMENT_CAP]}\n</comment>")
    return "\n".join(lines)


def vocabulary_block(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "(empty)"


CARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "notetype": {"type": "string", "enum": ["Cloze", "Basic"]},
                    "front": {"type": "string"},
                    "back": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["notetype", "front", "back", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cards"],
    "additionalProperties": False,
}


def make_cards(llm, prompts: Prompts, title: str, url: str, page_text: str,
               highlights: list[Highlight], tag_vocabulary: list[str]) -> list[dict]:
    prompt = prompts.render(
        "cards",
        title=untrusted(title),
        url=untrusted(url),
        page_text=untrusted(page_text[:PAGE_TEXT_CAP]),
        highlights=highlights_block(highlights),
        vocabulary=vocabulary_block(tag_vocabulary),
    )
    cards = llm.json(prompt, CARDS_SCHEMA, max_tokens=prompts.max_tokens["cards"])["cards"]
    if not cards:
        raise ValueError("model returned no cards")
    return cards
