"""LLM extraction: prompting, drop-and-warn validation, dry-run behaviour.

All OpenRouter calls are mocked at the client boundary (OpenRouterClient) --
no test touches the network or needs an API key.
"""

import json
import logging
from unittest.mock import MagicMock

import pytest

from anki_cardgen.entries import SpeakerGender
from anki_cardgen.extraction import (
    ExtractionResult,
    RejectedEntry,
    estimate_tokens,
    extract_entries,
    parse_llm_entries,
)
from anki_cardgen.openrouter import OpenRouterError, OpenRouterResponse


def response(entries: list[dict], **usage) -> OpenRouterResponse:
    return OpenRouterResponse(
        content=json.dumps({"entries": entries}),
        prompt_tokens=usage.get("prompt_tokens", 100),
        completion_tokens=usage.get("completion_tokens", 50),
        cost=usage.get("cost", 0.002),
    )


VALID = {"target": "كيفك", "transliteration": "kifak", "translation": "how are you",
         "notes": None, "speaker_gender": "any", "variant": None}
MARKED_WITH_VARIANT = {
    "target": "أنا جوعان", "transliteration": "ana jou3aan", "translation": "I'm hungry",
    "notes": None, "speaker_gender": "male",
    "variant": {"target": "أنا جوعانة", "transliteration": "ana jou3aane"},
}


class TestParseLlmEntries:
    def test_valid_entry_round_trips(self):
        entries, rejected = parse_llm_entries(json.dumps({"entries": [VALID]}))
        assert len(entries) == 1
        assert rejected == ()
        assert entries[0].target == "كيفك"

    def test_generated_entries_are_flagged_unreviewed(self):
        """The whole point of the review gate: nothing from the LLM reaches a
        card without a human clearing it first."""
        entries, _ = parse_llm_entries(json.dumps({"entries": [VALID]}))
        assert entries[0].generated is True
        assert entries[0].reviewed is False

    def test_marked_entry_with_variant_round_trips(self):
        entries, rejected = parse_llm_entries(json.dumps({"entries": [MARKED_WITH_VARIANT]}))
        assert rejected == ()
        assert entries[0].speaker_gender is SpeakerGender.MALE
        assert entries[0].variant.target == "أنا جوعانة"

    def test_invalid_json_raises_immediately(self):
        """Not a per-entry problem -- the whole response is unusable."""
        with pytest.raises(OpenRouterError, match="not the expected JSON shape"):
            parse_llm_entries("not json at all")

    def test_missing_entries_key_raises(self):
        with pytest.raises(OpenRouterError):
            parse_llm_entries(json.dumps({"oops": []}))

    def test_entries_not_a_list_raises(self):
        with pytest.raises(OpenRouterError, match="not a list"):
            parse_llm_entries(json.dumps({"entries": "nope"}))


class TestDropAndWarn:
    """The decision: one malformed entry is dropped and logged, not fatal to
    the batch -- you already paid for the whole call."""

    def test_bad_speaker_gender_is_dropped_not_fatal(self):
        bad = {**VALID, "speaker_gender": "masc"}
        entries, rejected = parse_llm_entries(json.dumps({"entries": [VALID, bad]}))

        assert len(entries) == 1
        assert len(rejected) == 1
        assert isinstance(rejected[0], RejectedEntry)
        assert "masc" in rejected[0].reason

    def test_variant_without_marked_gender_is_dropped(self):
        """Entry's own invariant (variant requires marked speaker_gender)
        fires here and is caught, not left to explode the whole batch."""
        bad = {**VALID, "variant": {"target": "x", "transliteration": None}}
        entries, rejected = parse_llm_entries(json.dumps({"entries": [bad]}))

        assert entries == ()
        assert len(rejected) == 1

    def test_empty_target_is_dropped(self):
        bad = {**VALID, "target": "  "}
        entries, rejected = parse_llm_entries(json.dumps({"entries": [bad]}))
        assert entries == ()
        assert len(rejected) == 1

    def test_missing_required_field_is_dropped(self):
        bad = {k: v for k, v in VALID.items() if k != "translation"}
        entries, rejected = parse_llm_entries(json.dumps({"entries": [bad]}))
        assert entries == ()
        assert len(rejected) == 1

    def test_valid_entries_survive_alongside_a_dropped_one(self):
        entries, rejected = parse_llm_entries(
            json.dumps({"entries": [VALID, {**VALID, "speaker_gender": "bad"}, MARKED_WITH_VARIANT]})
        )
        assert len(entries) == 2
        assert len(rejected) == 1

    def test_rejection_is_logged(self, caplog):
        bad = {**VALID, "speaker_gender": "masc"}
        with caplog.at_level(logging.WARNING, logger="anki_cardgen.extraction"):
            parse_llm_entries(json.dumps({"entries": [bad]}))
        assert "dropped" in caplog.text


class TestEstimateTokens:
    def test_scales_with_length(self):
        assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)

    def test_never_zero(self):
        assert estimate_tokens("") >= 1


class TestExtractEntries:
    def test_dry_run_makes_no_client_call(self):
        client = MagicMock()
        result = extract_entries("some lesson text", "shami", model="a/b",
                                 client=client, dry_run=True)

        client.complete.assert_not_called()
        assert result.dry_run is True
        assert result.entries == ()
        assert result.prompt_tokens > 0

    def test_dry_run_reports_no_dollar_cost(self):
        """Completion length and current pricing are both unknown before the
        call -- a dry-run dollar figure would be fabricated, not estimated."""
        result = extract_entries("text", "shami", model="a/b", dry_run=True)
        assert result.cost is None
        assert "prompt tokens" in result.summary()
        assert "$" not in result.summary()

    def test_real_call_returns_parsed_entries_and_usage(self):
        client = MagicMock()
        client.complete.return_value = response([VALID])

        result = extract_entries("lesson text", "shami", model="a/b", client=client)

        assert len(result.entries) == 1
        assert result.prompt_tokens == 100
        assert result.cost == 0.002
        assert result.dry_run is False

    def test_passes_language_and_text_into_the_prompt(self):
        client = MagicMock()
        client.complete.return_value = response([])
        extract_entries("لا تنسَ", "shami", model="a/b", client=client)

        messages = client.complete.call_args[0][0]
        joined = " ".join(m["content"] for m in messages)
        assert "shami" in joined
        assert "لا تنسَ" in joined

    def test_uses_the_configured_model(self):
        client = MagicMock()
        client.complete.return_value = response([])
        extract_entries("text", "shami", model="my/chosen-model", client=client)
        assert client.complete.call_args.kwargs["model"] == "my/chosen-model"

    def test_requests_structured_output(self):
        """Relying on prompt instructions alone for JSON shape is fragile;
        response_format is what keeps parsing honest."""
        client = MagicMock()
        client.complete.return_value = response([])
        extract_entries("text", "shami", model="a/b", client=client)
        assert client.complete.call_args.kwargs["response_format"] is not None

    def test_summary_mentions_rejected_entries(self):
        client = MagicMock()
        client.complete.return_value = response([VALID, {**VALID, "speaker_gender": "x"}])
        result = extract_entries("text", "shami", model="a/b", client=client)
        assert "rejected" in result.summary()

    def test_no_client_is_constructed_for_a_dry_run(self, monkeypatch):
        """Constructing a real OpenRouterClient is harmless (lazy key read),
        but a dry run should not even try -- this pins that it doesn't."""
        import anki_cardgen.extraction as mod

        def boom(*a, **k):
            raise AssertionError("OpenRouterClient should not be constructed on a dry run")

        monkeypatch.setattr(mod, "OpenRouterClient", boom)
        extract_entries("text", "shami", model="a/b", dry_run=True)
