"""The slug registry: every slug ever issued, and the rules guarding reuse.

This is the most important component in the repo. A slug drives the Anki note
GUID, and the GUID is what makes re-importing a rebuilt deck *update* existing
notes instead of duplicating them. Rebinding a slug to different content
therefore does not fail loudly at import time -- it silently overwrites a note
the user may have hundreds of reviews invested in. Hence the rules below.

Three cases, from SPEC section 5:

* same slug, same content  -> no-op, reuse the slug
* same slug, different content -> hard error, stop the run
* same content, different slug -> loud warning, proceed

The asymmetry is deliberate. The first case is the normal weekly path. The
second corrupts review history and is unrecoverable once imported. The third is
merely wasteful -- it forks review history across two notes -- so it warns
rather than blocking a run that may be legitimate (homographs, or the same word
deliberately drilled in two contexts).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from .slugs import canonicalize, derive_slug, text_hash

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slugs (
    slug        TEXT PRIMARY KEY,
    language    TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    target_text TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slugs_content ON slugs (language, text_hash);
"""


class SlugCollisionError(Exception):
    """A slug was reused for different content. Hard stop.

    Carries the old and new text so the message can tell the user what actually
    changed -- a bare "collision on shami-kifak" is not actionable when the
    difference is a single diacritic.
    """

    def __init__(self, slug: str, existing_text: str, incoming_text: str) -> None:
        self.slug = slug
        self.existing_text = existing_text
        self.incoming_text = incoming_text
        super().__init__(
            f"slug {slug!r} is already bound to {existing_text!r} "
            f"but was requested for {incoming_text!r}. A slug is only ever "
            f"bound to one piece of content; issue a new slug instead."
        )


@dataclass(frozen=True)
class SlugRecord:
    """One row of the registry."""

    slug: str
    language: str
    text_hash: str
    target_text: str
    created_at: str


@dataclass(frozen=True)
class DuplicateContent:
    """Warning: this content already exists in the registry under another slug."""

    slug: str
    existing_slug: str
    target_text: str

    def message(self) -> str:
        return (
            f"content {self.target_text!r} is already registered as "
            f"{self.existing_slug!r} but is being issued a second slug "
            f"{self.slug!r}. This will fork review history across two notes. "
            f"Set the slug explicitly in entries.yaml to reuse the existing note."
        )


@dataclass(frozen=True)
class Assignment:
    """The outcome of asking the registry for a slug."""

    slug: str
    created: bool
    warnings: tuple[DuplicateContent, ...] = ()


class SlugRegistry:
    """SQLite-backed registry of every slug ever issued."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            path = Path(self.db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> SlugRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- reads ---------------------------------------------------------------

    def get(self, slug: str) -> SlugRecord | None:
        row = self._conn.execute(
            "SELECT * FROM slugs WHERE slug = ?", (slug,)
        ).fetchone()
        return SlugRecord(**row) if row else None

    def find_by_content(self, language: str, target: str) -> list[SlugRecord]:
        """Every slug issued for this exact content, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM slugs WHERE language = ? AND text_hash = ? "
            "ORDER BY created_at, slug",
            (language, text_hash(target)),
        ).fetchall()
        return [SlugRecord(**row) for row in rows]

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM slugs").fetchone()[0]

    # -- writes --------------------------------------------------------------

    def reserve(self, slug: str, language: str, target: str) -> Assignment:
        """Claim an explicit ``slug`` for ``target``.

        Used when entries.yaml already carries a slug -- either from a previous
        run, or hand-written to force reuse of an existing note.

        Raises SlugCollisionError if the slug is bound to different content.
        """
        incoming_hash = text_hash(target)
        existing = self.get(slug)

        if existing is None:
            warnings = self._content_warnings(slug, language, target)
            self._insert(slug, language, incoming_hash, target)
            return Assignment(slug=slug, created=True, warnings=warnings)

        if existing.text_hash == incoming_hash:
            return Assignment(slug=slug, created=False)

        raise SlugCollisionError(slug, existing.target_text, canonicalize(target))

    def assign(
        self, language: str, target: str, transliteration: str | None = None
    ) -> Assignment:
        """Derive and claim a slug for ``target``, uniquifying if needed.

        Walks ``base``, ``base-2``, ``base-3``... until it finds a slug that is
        either free or already bound to exactly this content. Unlike
        :meth:`reserve` this never raises a collision: an occupied base slug is
        a reason to pick the next suffix, not to stop the run.
        """
        incoming_hash = text_hash(target)
        base = derive_slug(language, target, transliteration)

        suffix = 1
        while True:
            candidate = base if suffix == 1 else f"{base}-{suffix}"
            existing = self.get(candidate)

            if existing is None:
                warnings = self._content_warnings(candidate, language, target)
                self._insert(candidate, language, incoming_hash, target)
                return Assignment(slug=candidate, created=True, warnings=warnings)

            if existing.text_hash == incoming_hash:
                # Already registered under this exact slug. Reuse it -- this is
                # the normal weekly path for an unchanged entry, and must not
                # warn or allocate a suffix.
                return Assignment(slug=candidate, created=False)

            suffix += 1

    # -- internals -----------------------------------------------------------

    def _content_warnings(
        self, slug: str, language: str, target: str
    ) -> tuple[DuplicateContent, ...]:
        """Build (and log) warnings for content already held by another slug."""
        warnings = tuple(
            DuplicateContent(
                slug=slug, existing_slug=record.slug, target_text=record.target_text
            )
            for record in self.find_by_content(language, target)
            if record.slug != slug
        )
        for warning in warnings:
            logger.warning("%s", warning.message())
        return warnings

    def _insert(self, slug: str, language: str, content_hash: str, target: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO slugs (slug, language, text_hash, target_text, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    slug,
                    language,
                    content_hash,
                    canonicalize(target),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
