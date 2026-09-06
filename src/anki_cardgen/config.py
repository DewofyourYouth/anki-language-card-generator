"""Configuration loading.

Everything that varies by language, deck, or voice lives in ``config.yaml`` so
the same tool works for a different language without code changes. Secrets are
never in here -- API keys come from the environment only.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_REGISTRY_PATH = "~/.anki_card_gen/registry.db"


@dataclass(frozen=True)
class Config:
    """Resolved configuration for a run.

    Only the keys this slice needs are typed out. The rest of SPEC section 11
    (voice ids, model slugs, style directives, image caps) lands with the
    pipelines that consume them -- typing them now would be guessing at shapes
    before there is any code to constrain them.
    """

    language: str
    deck_name: str
    anki_model_id: int
    anki_deck_id: int
    registry_db_path: Path
    note_type_name: str = "Language Card"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
        path = Path(path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"config file not found: {path}. Copy config.yaml from the repo "
                f"root and edit it, or pass an explicit path."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a YAML mapping at the top level")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        required = ("language", "deck_name", "anki_model_id", "anki_deck_id")
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"config is missing required key(s): {', '.join(missing)}")

        # Anki keys note types and decks by these ids. Regenerating one does not
        # error -- it silently creates a second, parallel note type or deck and
        # orphans every existing card, so they are validated as integers here
        # rather than coerced from whatever YAML happened to produce.
        ids = {}
        for key in ("anki_model_id", "anki_deck_id"):
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"config {key} must be an integer, got {value!r}")
            ids[key] = value

        return cls(
            language=str(data["language"]),
            deck_name=str(data["deck_name"]),
            anki_model_id=ids["anki_model_id"],
            anki_deck_id=ids["anki_deck_id"],
            note_type_name=str(data.get("note_type_name") or "Language Card"),
            registry_db_path=Path(
                str(data.get("registry_db_path") or DEFAULT_REGISTRY_PATH)
            ).expanduser(),
            raw=data,
        )
