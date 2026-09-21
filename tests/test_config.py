"""The two things a new user hits first: the key check and prompts.yaml."""
import pytest

from highlightanki.config import ConfigError, load_settings
from highlightanki.prompts import PromptsError, load_prompts


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "HLANKI_MODEL", "HLANKI_NOTES_FORMAT",
                 "HLANKI_DECK", "HLANKI_NOTES_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_missing_api_key_is_a_clear_error():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings(env_file=None)


def test_env_file_supplies_the_key_and_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test\nHLANKI_DECK=Study\n"
                   "HLANKI_NOTES_FORMAT=org\nHLANKI_NOTES_DIR=~/somewhere\n")
    s = load_settings(env_file=env)
    assert s.deck == "Study"
    assert s.notes_format == "org"
    assert not str(s.notes_dir).startswith("~")
    assert s.notes_dir.name == "somewhere"


def test_invalid_notes_format_is_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HLANKI_NOTES_FORMAT", "html")
    with pytest.raises(ConfigError, match="HLANKI_NOTES_FORMAT"):
        load_settings(env_file=None)


def test_prompts_missing_file(tmp_path):
    with pytest.raises(PromptsError, match="not found"):
        load_prompts(tmp_path / "nope.yaml")


def test_prompts_missing_section_names_it(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("shared:\n  untrusted_note: a\n  comment_rule: b\ncards:\n  prompt: x\n")
    with pytest.raises(PromptsError, match="zettel.prompt"):
        load_prompts(p)


FIELDS = dict(title="T", url="U", page_text="P", highlights="H", vocabulary="V")


def _yaml(cards_prompt: str) -> str:
    return ("shared:\n  untrusted_note: TRUST\n  comment_rule: RULE\n"
            f"cards:\n  max_tokens: 123\n  prompt: |\n    {cards_prompt}\n"
            "zettel:\n  prompt: Z $comment_rule\n")


def test_prompts_render_substitutes_and_keeps_cloze_braces(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(_yaml("$title / $untrusted_note / $$5 / {{c1::x}}"))
    prompts = load_prompts(p)
    assert prompts.render("cards", **FIELDS) == "T / TRUST / $5 / {{c1::x}}"
    assert prompts.render("zettel", **FIELDS) == "Z RULE"
    assert prompts.max_tokens == {"cards": 123, "zettel": 16000}


def test_prompts_reject_misspelled_placeholder_at_load(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(_yaml("$title $higlights"))
    with pytest.raises(PromptsError, match=r"cards\.prompt uses unknown placeholder \$higlights"):
        load_prompts(p)


def test_prompts_reject_stray_dollar_at_load(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(_yaml("costs $ 5"))
    with pytest.raises(PromptsError, match=r"stray '\$'"):
        load_prompts(p)


@pytest.mark.parametrize("bad", ["nope", "0", "-5"])
def test_prompts_reject_invalid_max_tokens_at_load(tmp_path, bad):
    p = tmp_path / "p.yaml"
    p.write_text(_yaml("$title").replace("max_tokens: 123", f"max_tokens: {bad}"))
    with pytest.raises(PromptsError, match=r"cards\.max_tokens must be a positive integer"):
        load_prompts(p)
