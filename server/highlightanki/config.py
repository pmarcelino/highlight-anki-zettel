"""Settings, read once at startup from the environment (and the project `.env`).

Every knob a user might want to change lives here or in `prompts.yaml`;
nothing is hardcoded in the modules that do the work. The Anthropic key is
read by the SDK itself from `ANTHROPIC_API_KEY`; we only check that it is set
so a missing key fails at startup with a useful message instead of on the
first right-click.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOTE_FORMATS = ("markdown", "org")
# Fixed by the `key` in extension/manifest.json; change both if you re-key a fork.
EXTENSION_ID = "neengcjabhhombdcejjnonclpdmbhiim"


class ConfigError(Exception):
    """A setting is missing or invalid; the message tells the user what to fix."""


@dataclass(frozen=True)
class Settings:
    model: str
    pending_dir: Path
    tags_path: Path
    notes_dir: Path
    notes_format: str
    deck: str
    prompts_path: Path
    extension_id: str


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


def load_settings(env_file: Path | None = PROJECT_ROOT / ".env") -> Settings:
    if env_file is not None:
        load_dotenv(env_file)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put "
            "your key in it (get one at https://console.anthropic.com/settings/keys)."
        )
    data_dir = Path.home() / "Documents" / "highlight-anki-zettel"
    notes_format = os.environ.get("HLANKI_NOTES_FORMAT", "markdown").strip().lower()
    if notes_format not in NOTE_FORMATS:
        raise ConfigError(
            f"HLANKI_NOTES_FORMAT must be one of {', '.join(NOTE_FORMATS)}; got {notes_format!r}"
        )
    return Settings(
        model=os.environ.get("HLANKI_MODEL", "claude-opus-4-8"),
        pending_dir=_path("HLANKI_PENDING_DIR", data_dir / "anki-cards" / "pending"),
        tags_path=_path("HLANKI_TAGS", data_dir / "anki-cards" / "tags.txt"),
        notes_dir=_path("HLANKI_NOTES_DIR", data_dir / "notes"),
        notes_format=notes_format,
        deck=os.environ.get("HLANKI_DECK", "Default").strip() or "Default",
        prompts_path=_path("HLANKI_PROMPTS", PROJECT_ROOT / "prompts.yaml"),
        extension_id=os.environ.get("HLANKI_EXTENSION_ID", "").strip() or EXTENSION_ID,
    )
