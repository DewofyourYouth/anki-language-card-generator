"""The `.apkg` writer.

Wraps `genanki`, which handles Anki's SQLite+zip package format. The wrapping
is thin on purpose -- the interesting logic (GUIDs, voices, variants) lives in
`notes.py` and is tested without ever building a package.

What genanki is *not* trusted for is verification: tests unzip the result and
assert against the embedded SQLite directly, rather than taking a successful
`write_to_file()` as proof the deck is correct (SPEC section 13).
"""

from pathlib import Path
from typing import Iterable, Sequence

import genanki

from .notes import CARD_TEMPLATES, CSS, FIELDS, Note


def build_model(model_id: int, name: str) -> genanki.Model:
    """Build the Anki note type.

    ``model_id`` comes from config and must never be regenerated: Anki keys the
    note type by this id, so a new one creates a second, parallel note type and
    every existing card stops matching.
    """
    return genanki.Model(
        model_id=model_id,
        name=name,
        fields=[{"name": field} for field in FIELDS],
        templates=[dict(template) for template in CARD_TEMPLATES],
        css=CSS,
        sort_field_index=FIELDS.index("Target"),
    )


def write_package(
    notes: Sequence[Note],
    out_path: str | Path,
    *,
    deck_id: int,
    deck_name: str,
    model_id: int,
    model_name: str,
    media_files: Iterable[str | Path] = (),
) -> Path:
    """Write ``notes`` to a `.apkg` at ``out_path`` and return the path.

    Media is embedded in the package so Anki imports it on the user's behalf.
    Nothing is ever written into the user's `collection.media` directly, and
    the live collection is never opened (invariant #3).
    """
    if not notes:
        raise ValueError("refusing to write a deck with no notes")

    model = build_model(model_id, model_name)
    deck = genanki.Deck(deck_id=deck_id, name=deck_name)

    for note in notes:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[note.fields[field] for field in FIELDS],
                guid=note.guid,
            )
        )

    package = genanki.Package(deck)
    package.media_files = [str(path) for path in media_files]

    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(out_path))
    return out_path
