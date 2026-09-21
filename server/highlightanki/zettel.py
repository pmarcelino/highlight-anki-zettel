"""Turn a page's blue highlights into atomic Zettelkasten notes via Claude.

Notes are atomic, titled by a lowercase assertion, written in the model's
own words, tagged (preferring the vocabulary already in your notes folder),
cross-linked to their siblings from the same batch, and stamped with a
`Source` line. Two on-disk formats:

- `markdown`: one `.md` per note with YAML front matter and `[[wikilinks]]`
  (Obsidian, Logseq, Zettlr, any plain-Markdown folder).
- `org`: one `.org` per note in org-roam v1 layout (`:ID:` drawer,
  `#+roam_tags:`, `[[id:...]]` links) for Emacs users.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .cards import PAGE_TEXT_CAP, highlights_block, untrusted, vocabulary_block
from .export import claim_path, source_of
from .models import Highlight
from .prompts import Prompts

VOCAB_CAP = 60

NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "related": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-based numbers of sibling notes this one relates to",
                    },
                },
                "required": ["title", "body", "tags", "related"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["notes"],
    "additionalProperties": False,
}


def make_notes(llm, prompts: Prompts, title: str, url: str, page_text: str,
               highlights: list[Highlight], tag_vocabulary: list[str]) -> list[dict]:
    prompt = prompts.render(
        "zettel",
        title=untrusted(title),
        url=untrusted(url),
        page_text=untrusted(page_text[:PAGE_TEXT_CAP]),
        highlights=highlights_block(highlights),
        vocabulary=vocabulary_block(tag_vocabulary),
    )
    notes = llm.json(prompt, NOTES_SCHEMA, max_tokens=prompts.max_tokens["zettel"])["notes"]
    if not notes:
        raise ValueError("model returned no notes")
    return notes


# ---- tag vocabulary ---------------------------------------------------------

_TAG_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
_MD_TAGS_LINE = re.compile(r"^tags:\s*\[(.*)\]\s*$")


def _org_tags(line: str) -> list[str]:
    if not line.startswith("#+roam_tags:"):
        return []
    rest = line[len("#+roam_tags:"):]
    return [(q or b).strip() for q, b in _TAG_TOKEN.findall(rest)]


def _md_tags(line: str) -> list[str]:
    m = _MD_TAGS_LINE.match(line)
    if not m:
        return []
    return [t.strip().strip('"').strip("'") for t in m.group(1).split(",")]


def scan_tags(notes_dir: Path, cap: int = VOCAB_CAP) -> list[str]:
    """Most-used tags across the notes folder, whichever format the files use."""
    notes_dir = Path(notes_dir)
    if not notes_dir.is_dir():
        return []
    counts: Counter[str] = Counter()
    for path in list(notes_dir.glob("*.org")) + list(notes_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            for tag in _org_tags(line) + _md_tags(line):
                if tag:
                    counts[tag] += 1
    return [t for t, _ in counts.most_common(cap)]


# ---- note file writing ------------------------------------------------------

def _filename_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "note"


def _oneline(text: str) -> str:
    """Model/page text destined for a metadata line: a newline there would let
    it break out into further front-matter keys or org directives."""
    return re.sub(r"\s+", " ", text).strip()


def _title(note: dict) -> str:
    return _oneline(note["title"]).lower()


def _label(title: str) -> str:
    """Link label: `]` or `|` would end a [[wikilink]] / [[id:..][label]] early."""
    return _oneline(re.sub(r"[\[\]|]", " ", title))


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = (_oneline(t).lower().replace('"', "") for t in tags)
    return list(dict.fromkeys(t for t in cleaned if t))


def _format_org_tags(tags: list[str]) -> str:
    return " ".join(f'"{t}"' if " " in t else t for t in _clean_tags(tags))


def _front_matter(note: dict, note_id: str, source: str) -> str:
    """YAML-escaped by the library, so no title/tag/source can inject keys."""
    meta = {"id": note_id, "title": _title(note), "tags": _clean_tags(note["tags"]),
            "source": _oneline(source)}
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                          default_flow_style=None, width=10**6).rstrip("\n")


def render_org(note: dict, note_id: str, source: str,
               related: list[tuple[str, str]]) -> str:
    """`related` pairs are (sibling id, sibling title)."""
    lines = [
        ":PROPERTIES:",
        f":ID:       {note_id}",
        ":END:",
        f"#+title: {_title(note)}",
        f"#+roam_tags: {_format_org_tags(note['tags'])}",
        "",
        note["body"].strip(),
    ]
    if related:
        lines += ["", "Related:"]
        lines += [f"- [[id:{rid}][{_label(rtitle)}]]" for rid, rtitle in related]
    lines += ["", f"Source: {source}", ""]
    return "\n".join(lines)


def render_markdown(note: dict, note_id: str, source: str,
                    related: list[tuple[str, str]]) -> str:
    """`related` pairs are (sibling file stem, sibling title)."""
    lines = [
        "---",
        _front_matter(note, note_id, source),
        "---",
        "",
        f"# {_title(note)}",
        "",
        note["body"].strip(),
    ]
    if related:
        lines += ["", "## Related", ""]
        lines += [f"- [[{stem}|{_label(rtitle)}]]" for stem, rtitle in related]
    lines += ["", f"Source: {source}", ""]
    return "\n".join(lines)


def write_notes(notes: list[dict], title: str, url: str, notes_dir: Path,
                fmt: str = "markdown", now: datetime | None = None) -> list[Path]:
    """Write one file per note; sibling Related links are symmetrized."""
    if fmt not in ("markdown", "org"):
        raise ValueError(f"unknown notes format: {fmt}")
    notes_dir = Path(notes_dir)
    notes_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    source = source_of(title, url)
    ext = ".md" if fmt == "markdown" else ".org"

    ids = [str(uuid.uuid4()) for _ in notes]
    paths = []
    for i, note in enumerate(notes):
        stamp = (now + timedelta(seconds=i)).strftime("%Y%m%d%H%M%S")
        paths.append(claim_path(notes_dir, f"{stamp}-{_filename_slug(note['title'])}", ext))
    stems = [p.stem for p in paths]

    links: list[set[int]] = [set() for _ in notes]
    for i, note in enumerate(notes):
        for ref in note.get("related", []):
            j = ref - 1  # model speaks 1-based
            if 0 <= j < len(notes) and j != i:
                links[i].add(j)
                links[j].add(i)  # cross-link siblings in both directions

    for i, note in enumerate(notes):
        titles = [_title(notes[j]) for j in sorted(links[i])]
        if fmt == "org":
            related = [(ids[j], t) for j, t in zip(sorted(links[i]), titles)]
            text = render_org(note, ids[i], source, related)
        else:
            related = [(stems[j], t) for j, t in zip(sorted(links[i]), titles)]
            text = render_markdown(note, ids[i], source, related)
        paths[i].write_text(text, encoding="utf-8")
    return paths
