"""`.apkg` validation.

Per SPEC section 13, correctness is asserted against the SQLite embedded in the
package -- never against genanki's return value. A writer that silently drops a
note, mangles a GUID, or writes the wrong deck id returns just as successfully
as one that works.
"""

import json
import sqlite3
import zipfile

import pytest

from anki_cardgen.deck import build_model, write_package
from anki_cardgen.entries import Entry, SpeakerGender, Variant
from anki_cardgen.notes import FIELDS, build_note

MODEL_ID = 2040212950
DECK_ID = 1678772260

FIELD_SEPARATOR = "\x1f"  # Anki packs a note's fields into one 0x1f-joined string

KIFAK = Entry(target="كيفك", transliteration="kifak", translation="how are you (m.)")
HUNGRY = Entry(
    target="أنا جوعان",
    transliteration="ana jou3aan",
    translation="I'm hungry",
    speaker_gender=SpeakerGender.MALE,
    variant=Variant(target="أنا جوعانة", transliteration="ana jou3aane"),
)


@pytest.fixture
def package(tmp_path):
    """Write a two-note deck and return a reader over its embedded SQLite."""
    notes = [build_note(KIFAK, "shami-kifak"), build_note(HUNGRY, "shami-jou3aan")]
    out = write_package(
        notes, tmp_path / "deck.apkg",
        deck_id=DECK_ID, deck_name="Shami Vocabulary",
        model_id=MODEL_ID, model_name="Shami Card",
    )

    extracted = tmp_path / "unzipped"
    with zipfile.ZipFile(out) as archive:
        archive.extractall(extracted)

    connection = sqlite3.connect(extracted / "collection.anki2")
    connection.row_factory = sqlite3.Row
    yield connection, extracted, notes
    connection.close()


class TestPackageStructure:
    def test_contains_collection_and_media_index(self, package):
        _, extracted, _ = package
        assert (extracted / "collection.anki2").is_file()
        assert (extracted / "media").is_file()

    def test_media_index_is_valid_json(self, package):
        _, extracted, _ = package
        assert json.loads((extracted / "media").read_text()) == {}


class TestNotesInTheDatabase:
    def test_every_note_is_present(self, package):
        connection, _, notes = package
        count = connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert count == len(notes)

    def test_guids_match_what_we_derived(self, package):
        """The invariant that makes re-import update instead of duplicate. If
        genanki ignored our guid and generated its own, only this catches it."""
        connection, _, notes = package
        stored = {row["guid"] for row in connection.execute("SELECT guid FROM notes")}
        assert stored == {note.guid for note in notes}

    def test_fields_round_trip_in_model_order(self, package):
        connection, _, notes = package
        by_guid = {note.guid: note for note in notes}

        for row in connection.execute("SELECT guid, flds FROM notes"):
            expected = [by_guid[row["guid"]].fields[name] for name in FIELDS]
            assert row["flds"].split(FIELD_SEPARATOR) == expected

    def test_arabic_survives_the_round_trip(self, package):
        connection, _, _ = package
        blob = " ".join(r["flds"] for r in connection.execute("SELECT flds FROM notes"))
        assert "كيفك" in blob
        assert "أنا جوعان" in blob

    def test_notes_use_the_configured_model_id(self, package):
        connection, _, _ = package
        mids = {r["mid"] for r in connection.execute("SELECT mid FROM notes")}
        assert mids == {MODEL_ID}

    def test_cards_land_in_the_configured_deck(self, package):
        connection, _, notes = package
        rows = connection.execute("SELECT did FROM cards").fetchall()
        assert {r["did"] for r in rows} == {DECK_ID}
        assert len(rows) == len(notes)  # one template, so one card per note


class TestModelInTheDatabase:
    def test_model_declares_every_field_in_order(self, package):
        connection, _, _ = package
        models = json.loads(connection.execute("SELECT models FROM col").fetchone()[0])
        model = models[str(MODEL_ID)]
        assert [f["name"] for f in model["flds"]] == list(FIELDS)

    def test_no_qfmt_in_the_built_package_contains_mnemonic(self, package):
        """Invariant #4, asserted against the shipped artefact rather than the
        source constant -- this is what actually reaches the user's collection."""
        connection, _, _ = package
        models = json.loads(connection.execute("SELECT models FROM col").fetchone()[0])

        for model in models.values():
            for template in model["tmpls"]:
                assert "Mnemonic" not in template["qfmt"]

    def test_mnemonic_survives_on_a_back_template(self, package):
        connection, _, _ = package
        models = json.loads(connection.execute("SELECT models FROM col").fetchone()[0])
        model = models[str(MODEL_ID)]
        assert any("{{Mnemonic}}" in t["afmt"] for t in model["tmpls"])

    def test_sort_field_is_target(self, package):
        connection, _, _ = package
        models = json.loads(connection.execute("SELECT models FROM col").fetchone()[0])
        assert models[str(MODEL_ID)]["sortf"] == FIELDS.index("Target")


class TestGuidStabilityAcrossBuilds:
    def test_two_separate_builds_produce_identical_guids(self, tmp_path):
        """SPEC section 13: the regression test for invariant #2. Two builds,
        two packages, same slugs -- the GUIDs must match or re-import
        duplicates every note and loses its review history."""
        guids = []
        for run in ("first", "second"):
            out = write_package(
                [build_note(KIFAK, "shami-kifak")], tmp_path / f"{run}.apkg",
                deck_id=DECK_ID, deck_name="D", model_id=MODEL_ID, model_name="M",
            )
            extracted = tmp_path / run
            with zipfile.ZipFile(out) as archive:
                archive.extractall(extracted)
            connection = sqlite3.connect(extracted / "collection.anki2")
            guids.append(connection.execute("SELECT guid FROM notes").fetchone()[0])
            connection.close()

        assert guids[0] == guids[1]

    def test_editing_the_text_under_one_slug_does_not_move_the_guid(self, tmp_path):
        """Changing a card's content must update the existing note, not orphan
        it. (The registry separately makes this a hard error -- see
        test_registry.py -- but the GUID must be stable regardless.)"""
        edited = Entry(target="كيفكم", transliteration="kifkom", translation="how are you (pl.)")
        first = build_note(KIFAK, "shami-kifak")
        second = build_note(edited, "shami-kifak")
        assert first.guid == second.guid


class TestWriterGuards:
    def test_refuses_an_empty_deck(self, tmp_path):
        with pytest.raises(ValueError, match="no notes"):
            write_package([], tmp_path / "d.apkg", deck_id=DECK_ID, deck_name="D",
                          model_id=MODEL_ID, model_name="M")

    def test_creates_missing_parent_directories(self, tmp_path):
        out = write_package(
            [build_note(KIFAK, "shami-kifak")], tmp_path / "a" / "b" / "d.apkg",
            deck_id=DECK_ID, deck_name="D", model_id=MODEL_ID, model_name="M",
        )
        assert out.is_file()

    def test_model_uses_the_id_it_is_given(self):
        assert build_model(12345, "M").model_id == 12345
