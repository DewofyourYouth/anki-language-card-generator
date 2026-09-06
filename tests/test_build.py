"""Pipeline 1 step 2, end to end: approved entries -> registry -> notes -> apkg."""

import sqlite3
import zipfile

import pytest

from anki_cardgen.build import UnreviewedEntriesError, assign_slugs, build_deck
from anki_cardgen.config import Config
from anki_cardgen.entries import Entry, EntryDocument, SpeakerGender, Variant
from anki_cardgen.registry import SlugCollisionError, SlugRegistry

CONFIG = Config.from_dict({
    "language": "shami",
    "deck_name": "Shami Vocabulary",
    "anki_model_id": 2040212950,
    "anki_deck_id": 1678772260,
    "note_type_name": "Shami Card",
})


def document(*entries):
    return EntryDocument(language="shami", entries=tuple(entries))


KIFAK = Entry(target="كيفك", transliteration="kifak", translation="how are you (m.)")
MNIH = Entry(target="منيح", transliteration="mnih", translation="good / fine")
HUNGRY = Entry(
    target="أنا جوعان", transliteration="ana jou3aan", translation="I'm hungry",
    speaker_gender=SpeakerGender.MALE,
    variant=Variant(target="أنا جوعانة", transliteration="ana jou3aane"),
)


@pytest.fixture
def registry(tmp_path):
    with SlugRegistry(tmp_path / "registry.db") as reg:
        yield reg


class TestSlugAssignment:
    def test_derives_slugs_for_entries_without_one(self, registry):
        assignments = assign_slugs(document(KIFAK, MNIH), registry)
        assert [a.slug for a in assignments] == ["shami-kifak", "shami-mnih"]
        assert all(a.created for a in assignments)

    def test_honours_an_explicit_slug(self, registry):
        entry = Entry(target="كيفك", translation="how are you", slug="shami-custom")
        assert assign_slugs(document(entry), registry)[0].slug == "shami-custom"

    def test_explicit_slug_bound_to_other_text_is_a_hard_error(self, registry):
        registry.reserve("shami-taken", "shami", "منيح")
        entry = Entry(target="كيفك", translation="how are you", slug="shami-taken")

        with pytest.raises(SlugCollisionError):
            assign_slugs(document(entry), registry)

    def test_rerunning_reuses_slugs_rather_than_suffixing(self, registry):
        first = assign_slugs(document(KIFAK, MNIH), registry)
        second = assign_slugs(document(KIFAK, MNIH), registry)

        assert [a.slug for a in first] == [a.slug for a in second]
        assert not any(a.created for a in second)
        assert len(registry) == 2

    def test_hashes_the_primary_form_not_the_voice_selected_one(self, registry):
        """Content identity must not depend on which voice the slug drew."""
        assign_slugs(document(HUNGRY), registry)
        record = registry.find_by_content("shami", "أنا جوعان")
        assert len(record) == 1


class TestBuild:
    def test_writes_an_importable_package(self, registry, tmp_path):
        result = build_deck(document(KIFAK, MNIH), CONFIG, registry,
                            tmp_path / "deck.apkg")

        assert result.apkg_path.is_file()
        with zipfile.ZipFile(result.apkg_path) as archive:
            assert "collection.anki2" in archive.namelist()

    def test_notes_reach_the_database(self, registry, tmp_path):
        result = build_deck(document(KIFAK, MNIH), CONFIG, registry,
                            tmp_path / "deck.apkg")
        with zipfile.ZipFile(result.apkg_path) as archive:
            archive.extractall(tmp_path / "x")

        connection = sqlite3.connect(tmp_path / "x" / "collection.anki2")
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2
        connection.close()

    def test_reports_new_slugs_by_creation_not_position(self, registry, tmp_path):
        """New and pre-existing entries interleave; the count must track which
        slugs were actually created, not where they sit in the file."""
        build_deck(document(MNIH), CONFIG, registry, tmp_path / "a.apkg")
        result = build_deck(document(KIFAK, MNIH, HUNGRY), CONFIG, registry,
                            tmp_path / "b.apkg")

        assert set(result.new_slugs) == {"shami-kifak", "shami-ana-jou3aan"}

    def test_audio_is_attached_by_slug(self, registry, tmp_path):
        result = build_deck(
            document(KIFAK), CONFIG, registry, tmp_path / "d.apkg",
            audio={"shami-kifak": "[sound:kifak.mp3]"},
        )
        assert result.notes[0].fields["Audio"] == "[sound:kifak.mp3]"

    def test_notes_without_audio_are_left_empty(self, registry, tmp_path):
        result = build_deck(document(KIFAK), CONFIG, registry, tmp_path / "d.apkg")
        assert result.notes[0].fields["Audio"] == ""

    def test_language_mismatch_is_refused(self, registry, tmp_path):
        wrong = EntryDocument(language="fusha", entries=(KIFAK,))
        with pytest.raises(ValueError, match="does not match"):
            build_deck(wrong, CONFIG, registry, tmp_path / "d.apkg")


class TestReviewGate:
    def test_unreviewed_generated_entries_stop_the_build(self, registry, tmp_path):
        entry = Entry(target="أنا جوعانة", translation="I'm hungry",
                      generated=True, reviewed=False)
        with pytest.raises(UnreviewedEntriesError, match="not yet reviewed"):
            build_deck(document(entry), CONFIG, registry, tmp_path / "d.apkg")

    def test_error_lists_what_needs_reading(self, registry, tmp_path):
        entry = Entry(target="أنا جوعانة", translation="I'm hungry",
                      generated=True, reviewed=False)
        with pytest.raises(UnreviewedEntriesError) as exc:
            build_deck(document(entry), CONFIG, registry, tmp_path / "d.apkg")

        assert "أنا جوعانة" in str(exc.value)

    def test_gate_runs_before_any_slug_is_issued(self, registry, tmp_path):
        """A refused run must leave no trace in the registry, or the next
        attempt inherits slugs issued for content nobody approved."""
        entry = Entry(target="أنا جوعانة", translation="I'm hungry",
                      generated=True, reviewed=False)
        with pytest.raises(UnreviewedEntriesError):
            build_deck(document(KIFAK, entry), CONFIG, registry, tmp_path / "d.apkg")

        assert len(registry) == 0


class TestDryRun:
    def test_writes_nothing(self, registry, tmp_path):
        out = tmp_path / "deck.apkg"
        result = build_deck(document(KIFAK, MNIH), CONFIG, registry, out, dry_run=True)

        assert result.dry_run is True
        assert result.apkg_path is None
        assert not out.exists()

    def test_still_reports_what_would_be_built(self, registry, tmp_path):
        result = build_deck(document(KIFAK, MNIH), CONFIG, registry, dry_run=True)

        assert len(result.notes) == 2
        assert set(result.new_slugs) == {"shami-kifak", "shami-mnih"}
        assert "Would build 2 note(s)" in result.summary()

    def test_needs_no_output_path(self, registry):
        assert build_deck(document(KIFAK), CONFIG, registry, dry_run=True).notes

    def test_requires_output_path_when_not_dry_running(self, registry):
        with pytest.raises(ValueError, match="out_path is required"):
            build_deck(document(KIFAK), CONFIG, registry)


class TestResultSummary:
    def test_voice_split_counts_both_voices(self, registry, tmp_path):
        entries = [
            Entry(target=f"كلمة{i}", transliteration=f"kilme{i}", translation=f"word {i}")
            for i in range(40)
        ]
        result = build_deck(document(*entries), CONFIG, registry, dry_run=True)

        assert sum(result.voice_split.values()) == 40
        assert set(result.voice_split) == {"male", "female"}

    def test_summary_mentions_warnings(self, registry, tmp_path):
        registry.reserve("shami-other", "shami", "كيفك")
        result = build_deck(document(KIFAK), CONFIG, registry, dry_run=True)

        assert result.warnings
        assert "duplicate-content warning" in result.summary()
