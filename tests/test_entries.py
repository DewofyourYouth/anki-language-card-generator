"""entries.yaml schema, validation, and round-trip."""

import pytest

from anki_cardgen.entries import Entry, EntryDocument, SpeakerGender, Variant

DOC = {
    "language": "shami",
    "source": "lessons/2026-09-05.md",
    "entries": [
        {"target": "كيفك", "transliteration": "kifak", "translation": "how are you (m.)"},
        {
            "target": "أنا جوعان",
            "translation": "I'm hungry",
            "speaker_gender": "male",
            "variant": {"target": "أنا جوعانة", "transliteration": "ana jou3aane"},
        },
    ],
}


class TestSpeakerGender:
    def test_any_is_unmarked(self):
        assert not SpeakerGender.ANY.is_marked
        assert SpeakerGender.MALE.is_marked

    def test_opposite(self):
        assert SpeakerGender.MALE.opposite() is SpeakerGender.FEMALE
        assert SpeakerGender.FEMALE.opposite() is SpeakerGender.MALE

    def test_any_has_no_opposite(self):
        with pytest.raises(ValueError):
            SpeakerGender.ANY.opposite()


class TestEntryValidation:
    def test_rejects_empty_target(self):
        with pytest.raises(ValueError, match="target"):
            Entry(target="  ", translation="x")

    def test_rejects_missing_translation(self):
        with pytest.raises(ValueError, match="translation"):
            Entry(target="كيفك", translation="")

    def test_rejects_variant_on_unmarked_entry(self):
        """A variant with speaker_gender 'any' means someone misunderstood the
        field -- most likely as addressee gender, which belongs in notes."""
        with pytest.raises(ValueError, match="speaker_gender is 'any'"):
            Entry(
                target="كيفك",
                translation="how are you",
                variant=Variant(target="كيفِك"),
            )

    def test_marked_entry_without_variant_is_allowed(self):
        # A lesson may legitimately teach only one form.
        entry = Entry(target="أنا جوعان", translation="I'm hungry",
                      speaker_gender=SpeakerGender.MALE)
        assert entry.variant is None


class TestReviewGate:
    def test_generated_and_unreviewed_needs_review(self):
        entry = Entry(target="x", translation="y", generated=True, reviewed=False)
        assert entry.needs_review

    def test_hand_written_entries_never_need_review(self):
        assert not Entry(target="x", translation="y").needs_review

    def test_document_lists_unreviewed(self):
        doc = EntryDocument(
            language="shami",
            entries=(
                Entry(target="a", translation="b"),
                Entry(target="c", translation="d", generated=True, reviewed=False),
            ),
        )
        assert [e.target for e in doc.unreviewed()] == ["c"]


class TestDocumentParsing:
    def test_parses_a_full_document(self):
        doc = EntryDocument.from_dict(DOC)
        assert doc.language == "shami"
        assert len(doc.entries) == 2
        assert doc.entries[1].variant.target == "أنا جوعانة"
        assert doc.entries[1].speaker_gender is SpeakerGender.MALE

    def test_requires_language(self):
        with pytest.raises(ValueError, match="language"):
            EntryDocument.from_dict({"entries": [{"target": "a", "translation": "b"}]})

    def test_requires_non_empty_entries(self):
        with pytest.raises(ValueError, match="entries"):
            EntryDocument.from_dict({"language": "shami", "entries": []})

    def test_rejects_unknown_keys(self):
        """A typo'd key silently dropped here forks review history -- e.g.
        `slugs:` instead of `slug:` reissues a new slug for an existing note."""
        with pytest.raises(ValueError, match="unknown key"):
            EntryDocument.from_dict({
                "language": "shami",
                "entries": [{"target": "a", "translation": "b", "slugs": "oops"}],
            })

    def test_rejects_invalid_speaker_gender(self):
        with pytest.raises(ValueError, match="speaker_gender"):
            EntryDocument.from_dict({
                "language": "shami",
                "entries": [{"target": "a", "translation": "b", "speaker_gender": "masc"}],
            })

    def test_rejects_malformed_variant(self):
        with pytest.raises(ValueError, match="variant"):
            EntryDocument.from_dict({
                "language": "shami",
                "entries": [{
                    "target": "a", "translation": "b",
                    "speaker_gender": "male", "variant": "أنا جوعانة",
                }],
            })


class TestRoundTrip:
    def test_survives_dump_and_load(self, tmp_path):
        original = EntryDocument.from_dict(DOC)
        path = tmp_path / "entries.yaml"
        original.dump(path)

        assert EntryDocument.load(path) == original

    def test_arabic_is_written_verbatim_not_escaped(self, tmp_path):
        """This file is read by a human. \\u0643\\u064a\\u0641\\u0643 is not reviewable."""
        path = tmp_path / "entries.yaml"
        EntryDocument.from_dict(DOC).dump(path)
        text = path.read_text(encoding="utf-8")

        assert "كيفك" in text
        assert "\\u06" not in text

    def test_slug_key_is_always_emitted(self, tmp_path):
        """Even when null -- it is the field the user hand-edits to force reuse,
        so it must be visible in the file rather than something to remember."""
        path = tmp_path / "entries.yaml"
        EntryDocument.from_dict(DOC).dump(path)
        assert "slug:" in path.read_text(encoding="utf-8")

    def test_with_slugs_fills_by_index(self):
        doc = EntryDocument.from_dict(DOC).with_slugs({0: "shami-kifak"})
        assert doc.entries[0].slug == "shami-kifak"
        assert doc.entries[1].slug is None
