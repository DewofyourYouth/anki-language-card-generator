"""GUID derivation, voice assignment, variant selection, template invariants."""

import subprocess
import sys

import pytest

from anki_cardgen.entries import Entry, SpeakerGender, Variant
from anki_cardgen.notes import (
    CARD_TEMPLATES,
    FIELDS,
    Voice,
    assign_voice,
    build_note,
    guid_for_slug,
    select_forms,
)

HUNGRY_M = Entry(
    target="أنا جوعان",
    transliteration="ana jou3aan",
    translation="I'm hungry",
    speaker_gender=SpeakerGender.MALE,
    variant=Variant(target="أنا جوعانة", transliteration="ana jou3aane"),
)
HUNGRY_M_NO_VARIANT = Entry(
    target="أنا جوعان",
    translation="I'm hungry",
    speaker_gender=SpeakerGender.MALE,
)
KIFAK = Entry(target="كيفك", transliteration="kifak", translation="how are you (m.)")


class TestGuidStability:
    """Invariant #2. A GUID that moves silently duplicates notes on re-import,
    losing review history with no error and no undo."""

    def test_same_slug_same_guid(self):
        assert guid_for_slug("shami-kifak") == guid_for_slug("shami-kifak")

    def test_different_slug_different_guid(self):
        assert guid_for_slug("shami-kifak") != guid_for_slug("shami-mnih")

    def test_stable_across_separate_processes(self):
        """The regression test that matters: a GUID derived from Python's
        salted hash() would pass every in-process test and still corrupt a
        deck on the next run."""
        script = (
            "from anki_cardgen.notes import guid_for_slug;"
            "print(guid_for_slug('shami-kifak'))"
        )
        runs = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, check=True,
                env={"PYTHONHASHSEED": seed, "PATH": ""},
            ).stdout.strip()
            for seed in ("0", "1", "random")
        }
        assert len(runs) == 1
        assert runs.pop() == guid_for_slug("shami-kifak")

    def test_depends_on_slug_alone(self):
        """Not the text, not the language, not the fields."""
        a = build_note(KIFAK, "shami-kifak")
        b = build_note(
            Entry(target="totally different", translation="different too"),
            "shami-kifak",
        )
        assert a.guid == b.guid

    def test_rejects_empty_slug(self):
        with pytest.raises(ValueError):
            guid_for_slug("")


class TestVoiceAssignment:
    def test_deterministic(self):
        assert assign_voice("shami-kifak", KIFAK) == assign_voice("shami-kifak", KIFAK)

    def test_marked_entry_without_variant_is_constrained(self):
        # Only one form of the text exists; the other voice would say it wrong.
        assert assign_voice("shami-x", HUNGRY_M_NO_VARIANT) is Voice.MALE

    def test_marked_entry_with_variant_is_not_constrained(self):
        # Both forms exist, so parity decides and the text follows the voice.
        voices = {assign_voice(f"shami-{i}", HUNGRY_M) for i in range(50)}
        assert voices == {Voice.MALE, Voice.FEMALE}

    def test_split_is_roughly_even(self):
        slugs = [f"shami-word-{i}" for i in range(1000)]
        female = sum(1 for s in slugs if assign_voice(s, KIFAK) is Voice.FEMALE)
        assert 400 < female < 600

    def test_voice_does_not_depend_on_other_entries(self):
        """Batch-balancing was rejected precisely so this holds: inserting a
        word must not reshuffle voices and invalidate the whole TTS cache."""
        before = [assign_voice(f"shami-{i}", KIFAK) for i in range(20)]
        assign_voice("shami-brand-new", KIFAK)
        after = [assign_voice(f"shami-{i}", KIFAK) for i in range(20)]
        assert before == after


class TestVariantSelection:
    def test_unmarked_entry_has_no_alt_and_no_labels(self):
        forms = select_forms(KIFAK, Voice.FEMALE)
        assert forms.target == "كيفك"
        assert forms.alt_target is None
        assert forms.form_label == ""

    def test_matching_voice_keeps_primary_form(self):
        forms = select_forms(HUNGRY_M, Voice.MALE)
        assert forms.target == "أنا جوعان"
        assert forms.alt_target == "أنا جوعانة"
        assert (forms.form_label, forms.alt_label) == ("m.", "f.")

    def test_opposite_voice_swaps_in_the_variant(self):
        forms = select_forms(HUNGRY_M, Voice.FEMALE)
        assert forms.target == "أنا جوعانة"
        assert forms.transliteration == "ana jou3aane"
        assert forms.alt_target == "أنا جوعان"
        assert (forms.form_label, forms.alt_label) == ("f.", "m.")

    def test_spoken_text_always_matches_the_assigned_voice(self):
        """The whole point: a female voice must never read a masculine form."""
        for i in range(200):
            slug = f"shami-{i}"
            note = build_note(HUNGRY_M, slug)
            expected = "أنا جوعانة" if note.voice is Voice.FEMALE else "أنا جوعان"
            assert note.spoken_text == expected


class TestTemplateInvariants:
    """Invariant #4. A mnemonic hook on a prompt face gives away the answer."""

    def test_no_qfmt_contains_mnemonic(self):
        for template in CARD_TEMPLATES:
            assert "{{Mnemonic}}" not in template["qfmt"]
            assert "Mnemonic" not in template["qfmt"]

    def test_mnemonic_is_present_on_a_back(self):
        # Guards against the invariant being satisfied by dropping the field.
        assert any("{{Mnemonic}}" in t["afmt"] for t in CARD_TEMPLATES)

    def test_every_template_field_exists_in_the_model(self):
        import re

        for template in CARD_TEMPLATES:
            for side in ("qfmt", "afmt"):
                referenced = set(re.findall(r"\{\{[#/^]?(\w+)\}\}", template[side]))
                assert referenced <= set(FIELDS) | {"FrontSide"}


class TestBuildNote:
    def test_populates_every_declared_field(self):
        note = build_note(KIFAK, "shami-kifak")
        assert set(note.fields) == set(FIELDS)

    def test_carries_audio_and_mnemonic_through(self):
        note = build_note(KIFAK, "shami-kifak", audio="[sound:a.mp3]", mnemonic="<img>")
        assert note.fields["Audio"] == "[sound:a.mp3]"
        assert note.fields["Mnemonic"] == "<img>"

    def test_refuses_unreviewed_generated_entries(self):
        """LLM-authored grammar must not reach a card unreviewed."""
        entry = Entry(
            target="أنا جوعانة",
            translation="I'm hungry",
            generated=True,
            reviewed=False,
        )
        with pytest.raises(ValueError, match="not yet reviewed"):
            build_note(entry, "shami-x")

    def test_allows_generated_entries_once_reviewed(self):
        entry = Entry(
            target="أنا جوعانة", translation="I'm hungry",
            generated=True, reviewed=True,
        )
        assert build_note(entry, "shami-x").fields["Target"] == "أنا جوعانة"
