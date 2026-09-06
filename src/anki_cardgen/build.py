"""Pipeline 1, step 2: approved entries.yaml -> notes -> .apkg.

This is the unattended weekly path. It assumes the human review checkpoint has
already happened and refuses to proceed if it has not.

Ordering matters and is not arbitrary. Slugs are issued *before* any note is
built, because slug issuance is the step that can hard-error, and a run that
half-writes a deck before discovering a collision is worse than one that
refuses to start.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .deck import write_package
from .entries import Entry, EntryDocument
from .notes import Note, build_note
from .registry import Assignment, DuplicateContent, SlugRegistry

logger = logging.getLogger(__name__)


class UnreviewedEntriesError(Exception):
    """LLM-generated content reached the build without being reviewed.

    The entries.yaml checkpoint is the only gate between generated Arabic and
    audio that goes onto a card, so this stops the run rather than warning.
    """

    def __init__(self, entries: tuple[Entry, ...]) -> None:
        self.entries = entries
        listing = "\n".join(f"  - {e.target}  ({e.translation})" for e in entries)
        super().__init__(
            f"{len(entries)} generated entr"
            f"{'y is' if len(entries) == 1 else 'ies are'} not yet reviewed:\n"
            f"{listing}\n"
            f"Read them, correct anything wrong, then set `reviewed: true`."
        )


@dataclass(frozen=True)
class BuildResult:
    """What a build did, or -- under --dry-run -- what it would have done."""

    notes: tuple[Note, ...]
    warnings: tuple[DuplicateContent, ...] = ()
    new_slugs: tuple[str, ...] = ()
    apkg_path: Path | None = None
    dry_run: bool = False

    @property
    def voice_split(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for note in self.notes:
            counts[str(note.voice)] = counts.get(str(note.voice), 0) + 1
        return counts

    def summary(self) -> str:
        split = ", ".join(f"{v} {k}" for k, v in sorted(self.voice_split.items()))
        lines = [
            f"{'Would build' if self.dry_run else 'Built'} {len(self.notes)} note(s)"
            f" — {len(self.new_slugs)} new slug(s), voices: {split or 'none'}"
        ]
        if self.warnings:
            lines.append(f"{len(self.warnings)} duplicate-content warning(s)")
        if self.apkg_path:
            lines.append(f"wrote {self.apkg_path}")
        return "\n".join(lines)


def assign_slugs(
    document: EntryDocument, registry: SlugRegistry
) -> tuple[Assignment, ...]:
    """Issue a slug for every entry, in file order.

    An entry that already carries a slug is *reserved* -- the user named it, so
    a conflict is a hard error. An entry without one is *assigned*, which
    uniquifies with a numeric suffix instead of failing.

    The registry hashes ``entry.target``, the primary form as written in
    entries.yaml, never the voice-selected form. The primary form is the entry's
    stable identity; hashing whichever variant the voice happened to select
    would make content identity depend on the slug it is being issued for.
    """
    assignments: list[Assignment] = []

    for entry in document.entries:
        if entry.slug:
            assignments.append(
                registry.reserve(entry.slug, document.language, entry.target)
            )
        else:
            assignments.append(
                registry.assign(
                    document.language, entry.target, entry.transliteration
                )
            )

    return tuple(assignments)


def build_deck(
    document: EntryDocument,
    config: Config,
    registry: SlugRegistry,
    out_path: str | Path | None = None,
    *,
    dry_run: bool = False,
    audio: dict[str, str] | None = None,
    media_files: tuple[str | Path, ...] = (),
) -> BuildResult:
    """Turn an approved entries document into a deck.

    ``audio`` maps slug -> `[sound:...]` reference; it is empty until the TTS
    slice lands, and notes simply carry no audio until then.
    """
    unreviewed = document.unreviewed()
    if unreviewed:
        raise UnreviewedEntriesError(unreviewed)

    if document.language != config.language:
        # Not fatal in principle, but a mismatch means slugs are about to be
        # issued under a language the deck is not configured for, which is
        # almost always a wrong-file mistake.
        raise ValueError(
            f"entries document language {document.language!r} does not match "
            f"config language {config.language!r}"
        )

    assignments = assign_slugs(document, registry)
    warnings = tuple(w for a in assignments for w in a.warnings)
    new_slugs = tuple(a.slug for a in assignments if a.created)

    audio = audio or {}
    notes = tuple(
        build_note(entry, assignment.slug, audio=audio.get(assignment.slug, ""))
        for entry, assignment in zip(document.entries, assignments)
    )

    if dry_run:
        return BuildResult(
            notes=notes, warnings=warnings, new_slugs=new_slugs, dry_run=True
        )

    if out_path is None:
        raise ValueError("out_path is required unless dry_run=True")

    written = write_package(
        notes,
        out_path,
        deck_id=config.anki_deck_id,
        deck_name=config.deck_name,
        model_id=config.anki_model_id,
        model_name=config.note_type_name,
        media_files=media_files,
    )
    return BuildResult(
        notes=notes, warnings=warnings, new_slugs=new_slugs, apkg_path=written
    )
