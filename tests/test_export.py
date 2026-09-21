from datetime import datetime
from pathlib import Path

from highlightanki import export

CARDS = [
    {"notetype": "Cloze", "front": "The {{c1::testing effect}} says retrieval strengthens memory.",
     "back": "", "tags": ["Learning", "spaced repetition"]},
    {"notetype": "Basic", "front": "Why does retrieval\nbeat re-reading?",
     "back": "It forces\telaboration.", "tags": ["learning", "learning"]},
]


def test_to_tsv_headers_and_columns():
    tsv = export.to_tsv(CARDS, "My Page — https://x.test/a", "Study")
    lines = tsv.splitlines()
    assert lines[:5] == ["#separator:tab", "#html:false", "#notetype column:1",
                         "#deck column:2", "#tags column:6"]
    row = lines[5].split("\t")
    assert row[0] == "Cloze"
    assert row[1] == "Study"
    assert row[2] == "The {{c1::testing effect}} says retrieval strengthens memory."
    assert row[3] == ""
    assert row[4] == "My Page — https://x.test/a"
    assert row[5] == "learning spaced-repetition"
    assert len(row) == 6


def test_to_tsv_sanitizes_fields_and_dedupes_tags():
    row = export.to_tsv(CARDS, "s", "Default").splitlines()[6].split("\t")
    assert row[2] == "Why does retrieval beat re-reading?"
    assert row[3] == "It forces elaboration."
    assert row[5] == "learning"


def test_write_pending_filename_and_content(tmp_path: Path):
    now = datetime(2026, 8, 11, 9, 30, 0)
    path = export.write_pending(CARDS, "Why a Monorepo?!", "https://x.test/mono",
                                tmp_path / "pending", "Default", now=now)
    assert path.name == "20260811093000-why-a-monorepo.txt"
    text = path.read_text()
    assert "Why a Monorepo?! — https://x.test/mono" in text
    assert text.endswith("\n")


def test_write_pending_never_overwrites_same_second(tmp_path: Path):
    now = datetime(2026, 8, 11, 9, 30, 0)
    a = export.write_pending(CARDS[:1], "T", "https://u", tmp_path, "Default", now=now)
    b = export.write_pending(CARDS[1:], "T", "https://u", tmp_path, "Default", now=now)
    assert b.name == "20260811093000-t-2.txt"
    assert "Cloze" in a.read_text() and "Basic" in b.read_text()


def test_merge_tags_appends_sorts_dedupes(tmp_path: Path):
    tags = tmp_path / "tags.txt"
    tags.write_text("learning\nzettelkasten\n")
    export.merge_tags(tags, ["Learning", "ci-cd", "CI CD", "spaced repetition"])
    assert tags.read_text() == "ci-cd\nlearning\nspaced-repetition\nzettelkasten\n"


def test_merge_tags_creates_missing_file(tmp_path: Path):
    tags = tmp_path / "tags.txt"
    export.merge_tags(tags, ["b", "a"])
    assert tags.read_text() == "a\nb\n"


def test_read_tags_missing_file(tmp_path: Path):
    assert export.read_tags(tmp_path / "nope.txt") == []
