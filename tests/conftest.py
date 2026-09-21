import pytest

from highlightanki.config import PROJECT_ROOT, Settings
from highlightanki.prompts import load_prompts


class FakeLLM:
    def __init__(self, out=None, error=None):
        self.out = out
        self.error = error
        self.prompts = []

    def json(self, prompt, schema, max_tokens=8192):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.out


@pytest.fixture(scope="session")
def prompts():
    """The shipped prompts.yaml — so the suite proves it loads and renders."""
    return load_prompts(PROJECT_ROOT / "prompts.yaml")


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        model="test-model",
        pending_dir=tmp_path / "pending",
        tags_path=tmp_path / "tags.txt",
        notes_dir=tmp_path / "notes",
        notes_format="markdown",
        deck="Default",
        prompts_path=PROJECT_ROOT / "prompts.yaml",
        extension_id="abcdefghijklmnopabcdefghijklmnop",
    )
    return Settings(**{**base, **overrides})
