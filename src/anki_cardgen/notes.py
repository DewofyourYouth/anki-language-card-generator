"""Note construction: GUIDs, voice assignment, variant selection, templates.

Three separable concerns live here because they are all pure functions of an
entry plus its slug, with no I/O and no network:

* `guid_for_slug` -- the invariant that makes re-import update instead of duplicate
* `assign_voice` / `select_forms` -- which voice speaks, and therefore which
  gender variant of the text is the one on the card
* `NOTE_MODEL` -- fields and card templates
"""
import base64
import hashlib
from dataclasses import dataclass
from enum import StrEnum

from .entries import Entry, SpeakerGender

# Namespaced so that the GUID and the voice-parity bit are not both reading the
# same digest. Changing either constant silently rebinds every note in every
# existing deck, so they are frozen for the life of the tool.
_GUID_NAMESPACE = b"anki-cardgen:guid:"
_VOICE_NAMESPACE = b"anki-cardgen:voice:"

_GUID_BYTES = 10


class Voice(StrEnum):
    MALE = "male"
    FEMALE = "female"


def guid_for_slug(slug: str) -> str:
    """Derive an Anki note GUID from the slug, and nothing else.

    Not the text, not the language, not a timestamp, not the field contents.
    This is what makes re-running the pipeline over an edited entries.yaml
    update the existing note in place -- preserving its review history --
    rather than importing a duplicate.

    Deterministic across processes: SHA-256, never Python's salted `hash()`.
    """
    if not slug:
        raise ValueError("cannot derive a GUID from an empty slug")
    digest = hashlib.sha256(_GUID_NAMESPACE + slug.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:_GUID_BYTES]).decode("ascii").rstrip("=")


def assign_voice(slug: str, entry: Entry) -> Voice:
    """Pick the voice that speaks this note.

    Unmarked entries, and marked entries that carry a variant, split ~50-50 on
    the parity of the slug hash. Parity is used rather than balancing across the
    batch so that a voice depends only on its own slug: batch-balancing would
    mean adding one word to a lesson could reshuffle every other voice and
    invalidate the whole TTS cache.

    A marked entry with no variant is the one constrained case -- there is only
    one form of the text, so only one voice can say it without teaching the
    wrong grammar. Correctness wins over the even split.
    """
    if entry.speaker_gender.is_marked and entry.variant is None:
        return Voice(str(entry.speaker_gender))

    digest = hashlib.sha256(_VOICE_NAMESPACE + slug.encode("utf-8")).digest()
    return Voice.FEMALE if digest[0] & 1 else Voice.MALE


@dataclass(frozen=True)
class Forms:
    """Which text is spoken and shown, and which is the labelled counterpart."""

    target: str
    transliteration: str | None
    alt_target: str | None
    alt_transliteration: str | None
    form_label: str
    alt_label: str


_LABELS = {SpeakerGender.MALE: "m.", SpeakerGender.FEMALE: "f."}


def select_forms(entry: Entry, voice: Voice) -> Forms:
    """Choose the form matching ``voice``, demoting the other to the alt slot.

    For an unmarked entry there is only one form and no labels -- labelling a
    noun "(m.)" would imply a grammatical distinction that is not there.
    """
    if not entry.speaker_gender.is_marked:
        return Forms(entry.target, entry.transliteration, None, None, "", "")

    spoken_gender = SpeakerGender(str(voice))
    other_gender = spoken_gender.opposite()

    if entry.variant is None:
        # Constrained: assign_voice guarantees the voice matches the only form.
        return Forms(
            entry.target,
            entry.transliteration,
            None,
            None,
            _LABELS[entry.speaker_gender],
            "",
        )

    if entry.speaker_gender is spoken_gender:
        primary, alternate = (entry.target, entry.transliteration), (
            entry.variant.target,
            entry.variant.transliteration,
        )
    else:
        primary, alternate = (
            entry.variant.target,
            entry.variant.transliteration,
        ), (entry.target, entry.transliteration)

    return Forms(
        target=primary[0],
        transliteration=primary[1],
        alt_target=alternate[0],
        alt_transliteration=alternate[1],
        form_label=_LABELS[spoken_gender],
        alt_label=_LABELS[other_gender],
    )


# -- note model --------------------------------------------------------------

FIELDS = (
    "Target",
    "Transliteration",
    "Translation",
    "TargetAlt",
    "TranslitAlt",
    "FormLabel",
    "AltLabel",
    "SpeakerGender",
    "Notes",
    "Audio",
    "Mnemonic",
)

# `dir="auto"` rather than a hardcoded `direction: rtl`: the target language is
# configuration, so the card must lay itself out correctly for an RTL or an LTR
# script without a code change.
FRONT_TEMPLATE = """\
<div class="target" dir="auto">{{Target}}</div>
"""

# Invariant #4: {{Mnemonic}} must never appear above this line, i.e. never in a
# qfmt. A mnemonic hook on the prompt face gives away the answer. Regression
# test: tests/test_notes.py::TestTemplateInvariants
BACK_TEMPLATE = """\
{{FrontSide}}

<hr id="answer">

{{#Transliteration}}<div class="translit">{{Transliteration}}</div>{{/Transliteration}}
<div class="translation">{{Translation}}</div>

{{#TargetAlt}}
<div class="forms">
  <div class="form"><span class="label">{{FormLabel}}</span> <span dir="auto">{{Target}}</span></div>
  <div class="form"><span class="label">{{AltLabel}}</span> <span dir="auto">{{TargetAlt}}</span>{{#TranslitAlt}} <span class="translit">{{TranslitAlt}}</span>{{/TranslitAlt}}</div>
</div>
{{/TargetAlt}}

{{#Notes}}<div class="notes">{{Notes}}</div>{{/Notes}}

<div class="audio">{{Audio}}</div>

{{#Mnemonic}}<div class="mnemonic">{{Mnemonic}}</div>{{/Mnemonic}}
"""

CSS = """\
.card { font-family: -apple-system, system-ui, sans-serif; font-size: 20px;
        text-align: center; color: #1a1a1a; background: #fdfdfc; }
.target { font-size: 46px; line-height: 1.4; margin: 12px 0; }
.translit { font-size: 18px; color: #6b6b6b; font-style: italic; }
.translation { font-size: 24px; margin: 10px 0; }
.forms { margin: 16px auto; padding: 10px; max-width: 32em;
         border-top: 1px solid #e0e0dc; }
.form { font-size: 24px; margin: 6px 0; }
.form .label { font-size: 14px; color: #9a9a95; margin-inline-end: 6px; }
.notes { font-size: 15px; color: #6b6b6b; margin-top: 12px; }
.mnemonic img { max-width: 90%; max-height: 380px; margin-top: 14px;
                border-radius: 6px; }
"""

CARD_TEMPLATES = (
    {"name": "Recognition", "qfmt": FRONT_TEMPLATE, "afmt": BACK_TEMPLATE},
)


@dataclass(frozen=True)
class Note:
    """A fully resolved note, ready to hand to the deck writer."""

    guid: str
    slug: str
    voice: Voice
    fields: dict[str, str]

    @property
    def spoken_text(self) -> str:
        """The text TTS should synthesise -- the form actually on the card."""
        return self.fields["Target"]


def build_note(entry: Entry, slug: str, audio: str = "", mnemonic: str = "") -> Note:
    """Assemble a note from a reviewed entry and its registry-issued slug."""
    if entry.needs_review:
        raise ValueError(
            f"entry {entry.target!r} is LLM-generated and not yet reviewed; "
            f"clear `reviewed: true` in entries.yaml before building"
        )

    voice = assign_voice(slug, entry)
    forms = select_forms(entry, voice)

    return Note(
        guid=guid_for_slug(slug),
        slug=slug,
        voice=voice,
        fields={
            "Target": forms.target,
            "Transliteration": forms.transliteration or "",
            "Translation": entry.translation,
            "TargetAlt": forms.alt_target or "",
            "TranslitAlt": forms.alt_transliteration or "",
            "FormLabel": forms.form_label,
            "AltLabel": forms.alt_label,
            "SpeakerGender": str(entry.speaker_gender),
            "Notes": entry.notes or "",
            "Audio": audio,
            "Mnemonic": mnemonic,
        },
    )
