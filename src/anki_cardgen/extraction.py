"""LLM extraction: freeform lesson text -> reviewable entries.

Per SPEC section 3, this is the *one* LLM call in Pipeline 1, used only when
the structured fast path (parsing.py) declines the input. It produces the same
Entry/EntryDocument shapes the fast path does, so everything downstream
(review, slug assignment, note-building) is identical regardless of which path
an entry came from -- the only difference is that every entry produced here
carries `generated=True, reviewed=False` and must clear the review gate in
build.py before it can reach a card.

The model is asked to do extraction *and* speaker-gender/variant analysis in
one call -- not two -- because that is the one-LLM-call budget SPEC section 3
commits to. The prompt is explicit about the speaker-vs-addressee gender
distinction (SPEC section 4) because a naive prompt reliably conflates them.
"""

import json
import logging
from dataclasses import dataclass

from .entries import Entry, SpeakerGender, Variant
from .openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

# Rough estimate only -- used for the --dry-run cost preview before a real call
# is made, never for anything billed. Actual usage/cost after a real call comes
# from the API response itself, not this heuristic. A dedicated tokenizer
# dependency would make this exact for one model family and wrong for others,
# for a number that is only ever an estimate to begin with.
_CHARS_PER_TOKEN_ESTIMATE = 4

SYSTEM_PROMPT = """\
You extract vocabulary and phrases from language-lesson notes into structured \
JSON. The notes are informal and mix the target language with English \
explanations, in no particular order.

Return a JSON array. Each element has:
  - target (string, required): the target-language text, exactly as written
  - transliteration (string or null): a Latin-script transliteration, if the \
notes give or imply one
  - translation (string, required): the English meaning
  - notes (string or null): any usage note from the source (formality, \
context, grammar point) -- do not invent one if the source has none
  - speaker_gender ("male", "female", or "any"): "any" unless the phrase is \
first-person and the source language grammatically marks the SPEAKER's own \
gender in that form (e.g. Arabic "I'm hungry" inflecting for a male vs female \
speaker). This is NOT about the gender of who the phrase is addressed to --\
that is a completely different axis and must be ignored for this field.
  - variant (object or null): only when speaker_gender is "male" or "female", \
the same phrase inflected for the OTHER speaker gender, as \
{"target": ..., "transliteration": ...}. Null whenever speaker_gender is "any".

Extract every distinct vocabulary item, phrase, or example sentence you can \
find. Do not extract meta-commentary about the lesson itself. Return only the \
JSON array, no other text.
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "vocabulary_entries",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "transliteration": {"type": ["string", "null"]},
                            "translation": {"type": "string"},
                            "notes": {"type": ["string", "null"]},
                            "speaker_gender": {
                                "type": "string",
                                "enum": ["male", "female", "any"],
                            },
                            "variant": {
                                "type": ["object", "null"],
                                "properties": {
                                    "target": {"type": "string"},
                                    "transliteration": {"type": ["string", "null"]},
                                },
                                "required": ["target", "transliteration"],
                                "additionalProperties": False,
                            },
                        },
                        "required": [
                            "target", "transliteration", "translation",
                            "notes", "speaker_gender", "variant",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class RejectedEntry:
    """One item the model returned that did not validate. Never fatal alone --
    see the module docstring: the batch proceeds without it, loudly."""

    raw: dict
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    """For a real run, ``prompt_tokens``/``completion_tokens``/``cost`` are the
    API's own reported figures. For a dry run, ``prompt_tokens`` is instead the
    rough estimate from :func:`estimate_tokens` and the other two are left at
    their defaults -- completion length and current per-model pricing are both
    unknown before the call, so a dry-run dollar estimate would be fabricated
    precision rather than a real preview.
    """

    entries: tuple[Entry, ...]
    rejected: tuple[RejectedEntry, ...]
    prompt_tokens: int
    completion_tokens: int
    cost: float | None
    dry_run: bool = False

    def summary(self) -> str:
        lines = [
            f"{'Would extract' if self.dry_run else 'Extracted'} "
            f"{len(self.entries)} entr{'y' if len(self.entries) == 1 else 'ies'}"
        ]
        if self.rejected:
            lines.append(f"{len(self.rejected)} rejected (see warnings above)")
        if self.dry_run:
            lines.append(
                f"~{self.prompt_tokens} prompt tokens (rough estimate; actual "
                f"cost depends on completion length and current pricing, "
                f"neither known before the call)"
            )
        elif self.cost is not None:
            lines.append(f"cost: ${self.cost:.4f}")
        return " — ".join(lines)


def estimate_tokens(text: str) -> int:
    """A deliberately rough token estimate for dry-run cost previews only."""
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def build_messages(text: str, language: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Target language: {language}\n\nLesson notes:\n\n{text}",
        },
    ]


def parse_llm_entries(raw_content: str) -> tuple[tuple[Entry, ...], tuple[RejectedEntry, ...]]:
    """Validate the model's JSON against our own Entry schema.

    Per the drop-and-warn decision: a malformed item is logged and excluded,
    not fatal to the batch. Entry's own __post_init__ already enforces the
    invariants that matter (non-empty target/translation, variant requires a
    marked speaker_gender) -- this function's job is just to catch what those
    raise and keep going.
    """
    try:
        payload = json.loads(raw_content)
        raw_entries = payload["entries"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OpenRouterError(
            f"model response was not the expected JSON shape: {raw_content[:500]!r}"
        ) from exc

    if not isinstance(raw_entries, list):
        raise OpenRouterError("model response 'entries' was not a list")

    entries: list[Entry] = []
    rejected: list[RejectedEntry] = []

    for raw in raw_entries:
        try:
            entries.append(_entry_from_llm_dict(raw))
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append(RejectedEntry(raw=raw, reason=str(exc)))
            logger.warning("dropped LLM-extracted entry %r: %s", raw.get("target"), exc)

    return tuple(entries), tuple(rejected)


def _entry_from_llm_dict(raw: dict) -> Entry:
    if not isinstance(raw, dict):
        raise ValueError(f"expected an object, got {type(raw).__name__}")

    variant_raw = raw.get("variant")
    variant = None
    if variant_raw is not None:
        variant = Variant(
            target=str(variant_raw["target"]),
            transliteration=(variant_raw.get("transliteration") or None),
        )

    gender_raw = raw.get("speaker_gender", "any") or "any"
    try:
        gender = SpeakerGender(gender_raw)
    except ValueError:
        raise ValueError(f"invalid speaker_gender {gender_raw!r}") from None

    return Entry(
        target=str(raw["target"]),
        translation=str(raw["translation"]),
        transliteration=(raw.get("transliteration") or None),
        notes=(raw.get("notes") or None),
        speaker_gender=gender,
        variant=variant,
        generated=True,
        reviewed=False,
    )


def extract_entries(
    text: str,
    language: str,
    *,
    model: str,
    client: OpenRouterClient | None = None,
    dry_run: bool = False,
) -> ExtractionResult:
    """Run (or preview) the one LLM call that turns freeform text into entries."""
    messages = build_messages(text, language)

    if dry_run:
        prompt_tokens = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(
            f"Target language: {language}\n\nLesson notes:\n\n{text}"
        )
        return ExtractionResult(
            entries=(), rejected=(), prompt_tokens=prompt_tokens,
            completion_tokens=0, cost=None, dry_run=True,
        )

    client = client or OpenRouterClient()
    response = client.complete(
        messages, model=model, response_format=RESPONSE_FORMAT
    )
    entries, rejected = parse_llm_entries(response.content)

    return ExtractionResult(
        entries=entries,
        rejected=rejected,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost=response.cost,
    )
