"""Write cards to the pending folder in Anki's 6-column text import format.

    #separator:tab
    #html:false
    #notetype column:1
    #deck column:2
    #tags column:6
    Cloze|Basic <tab> <deck> <tab> front <tab> back <tab> source <tab> tag1 tag2

The file is the durable record: even when AnkiConnect imports the cards
straight into Anki, the file stays so nothing is ever lost.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

_tags_lock = threading.Lock()  # merge_tags is read-modify-write; jobs run concurrently

HEADER = (
    "#separator:tab\n"
    "#html:false\n"
    "#notetype column:1\n"
    "#deck column:2\n"
    "#tags column:6\n"
)


def _clean(field: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", field).strip()


def _slug(title: str, max_words: int = 4) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
    return "-".join(words[:max_words]) or "page"


def norm_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "", re.sub(r"[\s_]+", "-", tag.strip().lower())).strip("-")


def to_tsv(cards: list[dict], source: str, deck: str) -> str:
    rows = []
    for c in cards:
        tags = " ".join(dict.fromkeys(filter(None, (norm_tag(t) for t in c["tags"]))))
        rows.append("\t".join([
            c["notetype"], deck, _clean(c["front"]), _clean(c["back"]),
            _clean(source), tags,
        ]))
    return HEADER + "\n".join(rows) + "\n"


def claim_path(directory: Path, stem: str, ext: str) -> Path:
    """Create `stem.ext` (else `stem-2.ext`, `stem-3.ext`…) exclusively and return it.

    O_EXCL makes the claim atomic across the concurrent job threads, so the
    caller can write to the returned path without anyone else taking it.
    """
    n = 1
    while True:
        path = directory / (f"{stem}{ext}" if n == 1 else f"{stem}-{n}{ext}")
        try:
            path.open("x").close()
            return path
        except FileExistsError:
            n += 1


def write_pending(cards: list[dict], title: str, url: str, pending_dir: Path,
                  deck: str, now: datetime | None = None) -> Path:
    pending_dir = Path(pending_dir)
    pending_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    path = claim_path(pending_dir, f"{stamp}-{_slug(title)}", ".txt")
    path.write_text(to_tsv(cards, source_of(title, url), deck), encoding="utf-8")
    return path


def source_of(title: str, url: str) -> str:
    return _clean(f"{title} — {url}")


def read_tags(tags_path: Path) -> list[str]:
    tags_path = Path(tags_path)
    if not tags_path.exists():
        return []
    return sorted({t.strip() for t in tags_path.read_text(encoding="utf-8").splitlines() if t.strip()})


def merge_tags(tags_path: Path, new_tags: list[str]) -> None:
    """Append tags not already present (case-insensitive), keep the file sorted/deduped."""
    tags_path = Path(tags_path)
    with _tags_lock:
        existing = read_tags(tags_path)
        known = {t.lower() for t in existing}
        added = [t for t in dict.fromkeys(norm_tag(t) for t in new_tags) if t and t.lower() not in known]
        if not added and tags_path.exists():
            return
        tags_path.parent.mkdir(parents=True, exist_ok=True)
        tags_path.write_text("\n".join(sorted(existing + added)) + "\n", encoding="utf-8")
