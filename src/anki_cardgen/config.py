"""Configuration loading.

Everything that varies by language, deck, or voice lives in ``config.yaml`` so
the same tool works for a different language without code changes. Secrets are
never in here -- API keys come from the environment only.
"""

from __future__ import annotations

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
    registry_db_path: Path
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
        missing = [key for key in ("language", "deck_name") if not data.get(key)]
        if missing:
            raise ValueError(f"config is missing required key(s): {', '.join(missing)}")
        return cls(
            language=str(data["language"]),
            deck_name=str(data["deck_name"]),
            registry_db_path=Path(
                str(data.get("registry_db_path") or DEFAULT_REGISTRY_PATH)
            ).expanduser(),
            raw=data,
        )
