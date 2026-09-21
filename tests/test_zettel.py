import re
from datetime import datetime

import pytest
import yaml

from highlightanki import zettel
from highlightanki.models import Highlight


NOW = datetime(2026, 8, 11, 12, 0, 0)


def notes():
    return [
        {"title": "Resistance Sabotages Our Calling", "body": "Own words A.",
         "tags": ["steven pressfield", "discipline", "resistance"], "related": [2]},
        {"title": "groups pact to stay mediocre", "body": "Own words B.",
         "tags": ["Social Dynamics", "mediocrity"], "related": []},
    ]


def front_matter(text: str) -> dict:
    """The YAML block between the `---` fences, as a consumer (Obsidian) reads it."""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, text
    return yaml.safe_load(m.group(1))


# ---- org ---------------------------------------------------------------------

def test_write_notes_org_format(tmp_path):
    paths = zettel.write_notes(notes(), "Page T", "https://x.test/p", tmp_path, "org", now=NOW)
    text = paths[0].read_text()
    m = re.match(
        r":PROPERTIES:\n:ID:       ([0-9a-f-]{36})\n:END:\n"
        r"#\+title: resistance sabotages our calling\n"
        r'#\+roam_tags: "steven pressfield" discipline resistance\n'
        r"\nOwn words A\.\n", text)
    assert m, text
    assert text.endswith("\nSource: Page T — https://x.test/p\n")


def test_write_notes_org_cross_links_siblings_both_ways(tmp_path):
    paths = zettel.write_notes(notes(), "T", "https://u", tmp_path, "org", now=NOW)
    first, second = (p.read_text() for p in paths)
    id_of = lambda t: re.search(r":ID:       (\S+)", t).group(1)
    # note 1 declared related: [2]; both files must link each other
    assert f"- [[id:{id_of(second)}][groups pact to stay mediocre]]" in first
    assert f"- [[id:{id_of(first)}][resistance sabotages our calling]]" in second
    assert first.index("Related:") < first.index("Source:")


def test_write_notes_org_filenames_spaced_one_second(tmp_path):
    paths = zettel.write_notes(notes(), "T", "https://u", tmp_path, "org", now=NOW)
    assert paths[0].name == "20260811120000-resistance_sabotages_our_calling.org"
    assert paths[1].name == "20260811120001-groups_pact_to_stay_mediocre.org"


# ---- markdown ----------------------------------------------------------------

def test_write_notes_markdown_format(tmp_path):
    paths = zettel.write_notes(notes(), "Page T", "https://x.test/p", tmp_path, now=NOW)
    assert paths[0].name == "20260811120000-resistance_sabotages_our_calling.md"
    text = paths[0].read_text()
    meta = front_matter(text)
    assert re.fullmatch(r"[0-9a-f-]{36}", meta["id"])
    assert meta["title"] == "resistance sabotages our calling"
    assert meta["tags"] == ["steven pressfield", "discipline", "resistance"]
    assert meta["source"] == "Page T — https://x.test/p"
    assert list(meta) == ["id", "title", "tags", "source"]
    assert "\n---\n\n# resistance sabotages our calling\n\nOwn words A.\n" in text
    assert text.endswith("\nSource: Page T — https://x.test/p\n")


def test_write_notes_markdown_wikilinks_siblings_both_ways(tmp_path):
    paths = zettel.write_notes(notes(), "T", "https://u", tmp_path, now=NOW)
    first, second = (p.read_text() for p in paths)
    assert "- [[20260811120001-groups_pact_to_stay_mediocre|groups pact to stay mediocre]]" in first
    assert ("- [[20260811120000-resistance_sabotages_our_calling|"
            "resistance sabotages our calling]]") in second
    assert first.index("## Related") < first.index("Source:")
    assert "## Related" in second


def test_write_notes_markdown_omits_related_when_none(tmp_path):
    lone = [{"title": "t", "body": "b", "tags": ["x"], "related": []}]
    text = zettel.write_notes(lone, "T", "https://u", tmp_path, now=NOW)[0].read_text()
    assert "Related" not in text


def test_write_notes_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        zettel.write_notes(notes(), "T", "u", tmp_path, "html")


def test_write_notes_ignores_out_of_range_related(tmp_path):
    bad = [{"title": "t", "body": "b", "tags": ["x"], "related": [1, 7, 0]}]
    paths = zettel.write_notes(bad, "T", "https://u", tmp_path, "org", now=NOW)
    assert "Related:" not in paths[0].read_text()


EVIL = [{"title": 'x\ntags: [evil]\n---\n# injected \\ "q"', "body": "b",
         "tags": ["ok", "a]\nrun: [rm -rf]", 'b" #+begin_src elisp', "c|d]]"],
         "related": [2]},
        {"title": "second]] | title", "body": "b", "tags": [], "related": []}]


def test_markdown_front_matter_survives_model_controlled_fields(tmp_path):
    md = zettel.write_notes(EVIL, 'P\nage \\ "T"', "https://u", tmp_path, now=NOW)[0].read_text()
    meta = front_matter(md)
    assert list(meta) == ["id", "title", "tags", "source"]
    assert meta["title"] == 'x tags: [evil] --- # injected \\ "q"'
    assert meta["tags"] == ["ok", "a] run: [rm -rf]", "b #+begin_src elisp", "c|d]]"]
    assert meta["source"] == 'P age \\ "T" — https://u'
    assert "- [[20260811120001-second_title|second title]]" in md


def test_org_metadata_lines_survive_model_controlled_fields(tmp_path):
    org = zettel.write_notes(EVIL, "P\nage", "https://u", tmp_path, "org", now=NOW)[0].read_text()
    head, _, rest = org.partition("\n\n")
    lines = head.splitlines()
    assert lines[3] == '#+title: x tags: [evil] --- # injected \\ "q"'
    assert lines[4] == '#+roam_tags: ok "a] run: [rm -rf]" "b #+begin_src elisp" c|d]]'
    assert len(lines) == 5
    assert re.search(r"- \[\[id:[0-9a-f-]{36}\]\[second title\]\]", rest)


def test_write_notes_never_overwrites_an_existing_note(tmp_path):
    first = zettel.write_notes(notes(), "T", "https://u", tmp_path, now=NOW)
    second = zettel.write_notes(notes(), "T", "https://u", tmp_path, now=NOW)
    assert second[0].name == "20260811120000-resistance_sabotages_our_calling-2.md"
    assert "[[20260811120001-groups_pact_to_stay_mediocre-2|" in second[0].read_text()
    assert first[0].read_text() != "" and first[0].exists()


# ---- vocabulary --------------------------------------------------------------

def test_scan_tags_orders_by_frequency_across_formats(tmp_path):
    (tmp_path / "a.org").write_text(
        '#+title: a\n#+roam_tags: "steven pressfield" discipline\n')
    (tmp_path / "b.org").write_text(
        "#+title: b\n#+roam_tags: discipline habits\n")
    (tmp_path / "c.md").write_text(
        "---\nid: x\ntitle: \"c\"\ntags: [discipline, \"deep work\"]\n---\n\nbody\n")
    assert zettel.scan_tags(tmp_path) == [
        "discipline", "steven pressfield", "habits", "deep work"]


def test_scan_tags_missing_dir(tmp_path):
    assert zettel.scan_tags(tmp_path / "nope") == []


def test_make_notes_raises_on_empty(prompts):
    class FakeLLM:
        def json(self, prompt, schema, max_tokens=8192):
            return {"notes": []}

    with pytest.raises(ValueError):
        zettel.make_notes(FakeLLM(), prompts, "t", "u", "", [Highlight(text="h")], [])
