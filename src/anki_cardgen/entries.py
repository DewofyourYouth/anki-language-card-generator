"""The `entries.yaml` schema and its round-trip.

`entries.yaml` is the human review checkpoint between parsing and building. It
is the only place a person sees the extracted vocabulary before it becomes
cards, and -- since the LLM path authors gender variants -- the only gate
between generated Arabic and audio the user will hear hundreds of times.

The schema therefore optimises for *readability under review*, not for
compactness: explicit keys, no clever nesting, unicode written through
verbatim rather than escaped.
"""
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class SpeakerGender(StrEnum):
    """Whose gender the target text's grammar marks.

    This is the *speaker's* gender -- first-person self-reference, where
    Levantine inflects (أنا جوعان / أنا جوعانة). It is deliberately NOT the
    addressee's gender (كيفك / كيفِك), which is a different axis and belongs in
    `notes`. Conflating the two produces cards that teach the wrong form.
    """

    MALE = "male"
    FEMALE = "female"
    ANY = "any"

    @property
    def is_marked(self) -> bool:
        return self is not SpeakerGender.ANY

    def opposite(self) -> SpeakerGender:
        if self is SpeakerGender.MALE:
            return SpeakerGender.FEMALE
        if self is SpeakerGender.FEMALE:
            return SpeakerGender.MALE
        raise ValueError("SpeakerGender.ANY has no opposite")


@dataclass(frozen=True)
class Variant:
    """The opposite-gender form of a speaker-marked entry."""

    target: str
    transliteration: str | None = None


@dataclass(frozen=True)
class Entry:
    """One vocabulary item awaiting review."""

    target: str
    translation: str
    transliteration: str | None = None
    notes: str | None = None
    speaker_gender: SpeakerGender = SpeakerGender.ANY
    variant: Variant | None = None
    slug: str | None = None
    # LLM-generated content starts unreviewed and blocks the build until a
    # human clears it. Entries parsed from a structured file are never
    # generated, so they default to reviewed.
    generated: bool = False
    reviewed: bool = True

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("entry target must not be empty")
        if not self.translation.strip():
            raise ValueError(f"entry {self.target!r} has no translation")
        if self.variant is not None and not self.speaker_gender.is_marked:
            raise ValueError(
                f"entry {self.target!r} has a gender variant but "
                f"speaker_gender is 'any'. A variant only makes sense when the "
                f"text marks the speaker's gender."
            )

    @property
    def needs_review(self) -> bool:
        return self.generated and not self.reviewed


@dataclass(frozen=True)
class EntryDocument:
    """A whole `entries.yaml` file."""

    language: str
    entries: tuple[Entry, ...]
    source: str | None = None

    def unreviewed(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.needs_review)

    def with_slugs(self, slugs: dict[int, str]) -> EntryDocument:
        """Return a copy with slugs filled in by index."""
        return replace(
            self,
            entries=tuple(
                replace(e, slug=slugs.get(i, e.slug))
                for i, e in enumerate(self.entries)
            ),
        )

    # -- serialisation -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> EntryDocument:
        path = Path(path).expanduser()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a YAML mapping at the top level")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntryDocument:
        language = data.get("language")
        if not language:
            raise ValueError("entries document is missing 'language'")

        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("entries document must have a non-empty 'entries' list")

        return cls(
            language=str(language),
            source=data.get("source"),
            entries=tuple(_entry_from_dict(raw) for raw in raw_entries),
        )

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"language": self.language}
        if self.source:
            doc["source"] = self.source
        doc["entries"] = [_entry_to_dict(e) for e in self.entries]
        return doc

    def dump(self, path: str | Path) -> None:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_yaml(), encoding="utf-8")

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.to_dict(),
            allow_unicode=True,  # write Arabic verbatim; this file gets read by a human
            sort_keys=False,
            default_flow_style=False,
            width=88,
        )


def _entry_from_dict(raw: Any) -> Entry:
    if not isinstance(raw, dict):
        raise ValueError(f"each entry must be a mapping, got {type(raw).__name__}")

    unknown = set(raw) - {
        "target", "translation", "transliteration", "notes",
        "speaker_gender", "variant", "slug", "generated", "reviewed",
    }
    if unknown:
        # A typo'd key that is silently dropped is worse here than elsewhere:
        # this file is hand-edited, and a dropped `slug:` forks review history.
        raise ValueError(f"unknown key(s) in entry: {', '.join(sorted(unknown))}")

    if "target" not in raw:
        raise ValueError("entry is missing 'target'")
    if "translation" not in raw:
        raise ValueError(f"entry {raw['target']!r} is missing 'translation'")

    variant_raw = raw.get("variant")
    variant = None
    if variant_raw is not None:
        if not isinstance(variant_raw, dict) or "target" not in variant_raw:
            raise ValueError(
                f"entry {raw['target']!r} has a malformed 'variant' "
                f"(needs at least a 'target')"
            )
        variant = Variant(
            target=str(variant_raw["target"]),
            transliteration=_opt_str(variant_raw.get("transliteration")),
        )

    try:
        gender = SpeakerGender(str(raw.get("speaker_gender", "any")))
    except ValueError:
        raise ValueError(
            f"entry {raw['target']!r} has invalid speaker_gender "
            f"{raw.get('speaker_gender')!r}; expected male, female, or any"
        ) from None

    return Entry(
        target=str(raw["target"]),
        translation=str(raw["translation"]),
        transliteration=_opt_str(raw.get("transliteration")),
        notes=_opt_str(raw.get("notes")),
        speaker_gender=gender,
        variant=variant,
        slug=_opt_str(raw.get("slug")),
        generated=bool(raw.get("generated", False)),
        reviewed=bool(raw.get("reviewed", True)),
    )


def _entry_to_dict(entry: Entry) -> dict[str, Any]:
    out: dict[str, Any] = {"target": entry.target}
    if entry.transliteration:
        out["transliteration"] = entry.transliteration
    out["translation"] = entry.translation
    if entry.notes:
        out["notes"] = entry.notes
    out["speaker_gender"] = str(entry.speaker_gender)
    if entry.variant is not None:
        variant: dict[str, Any] = {"target": entry.variant.target}
        if entry.variant.transliteration:
            variant["transliteration"] = entry.variant.transliteration
        out["variant"] = variant
    if entry.generated:
        out["generated"] = True
        out["reviewed"] = entry.reviewed
    out["slug"] = entry.slug
    return out


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
