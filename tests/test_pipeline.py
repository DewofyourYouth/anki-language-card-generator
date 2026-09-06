"""End to end: raw file -> entries.yaml, fast path first, LLM only on decline."""

import json
from unittest.mock import MagicMock

import pytest

from anki_cardgen.entries import EntryDocument
from anki_cardgen.extraction import RESPONSE_FORMAT
from anki_cardgen.openrouter import OpenRouterResponse
from anki_cardgen.pipeline import OutputExistsError, extract_to_yaml

STRUCTURED = "كيفك,how are you\nمنيح,good\n"
FREEFORM = "Lesson notes today: كيفك means how are you, said informally.\nAlso came up: منيح.\n"

VALID_LLM_ENTRY = {
    "target": "كيفك", "transliteration": "kifak", "translation": "how are you",
    "notes": None, "speaker_gender": "any", "variant": None,
}


def llm_client(entries=(VALID_LLM_ENTRY,)):
    client = MagicMock()
    client.complete.return_value = OpenRouterResponse(
        content=json.dumps({"entries": list(entries)}),
        prompt_tokens=80, completion_tokens=40, cost=0.001,
    )
    return client


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestFastPathPrecedence:
    def test_structured_input_never_touches_the_llm(self, tmp_path):
        client = MagicMock()
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)

        result = extract_to_yaml(
            input_path, tmp_path / "entries.yaml", "shami", client=client
        )

        client.complete.assert_not_called()
        assert result.used_llm is False
        assert len(result.document.entries) == 2

    def test_structured_entries_are_not_flagged_generated(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        result = extract_to_yaml(input_path, tmp_path / "entries.yaml", "shami")
        assert all(not e.generated for e in result.document.entries)

    def test_fast_path_writes_entries_yaml(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"
        extract_to_yaml(input_path, out, "shami")

        assert out.is_file()
        assert EntryDocument.load(out).entries[0].target == "كيفك"


class TestLlmFallback:
    def test_freeform_input_falls_back_to_the_llm(self, tmp_path):
        client = llm_client()
        input_path = write(tmp_path, "lesson.md", FREEFORM)

        result = extract_to_yaml(
            input_path, tmp_path / "entries.yaml", "shami",
            llm_model="a/b", client=client,
        )

        client.complete.assert_called_once()
        assert result.used_llm is True
        assert len(result.document.entries) == 1

    def test_llm_entries_carry_the_review_gate(self, tmp_path):
        input_path = write(tmp_path, "lesson.md", FREEFORM)
        result = extract_to_yaml(
            input_path, tmp_path / "entries.yaml", "shami",
            llm_model="a/b", client=llm_client(),
        )
        assert result.document.entries[0].generated is True
        assert result.document.entries[0].reviewed is False

    def test_source_file_is_recorded(self, tmp_path):
        input_path = write(tmp_path, "lesson.md", FREEFORM)
        result = extract_to_yaml(
            input_path, tmp_path / "entries.yaml", "shami",
            llm_model="a/b", client=llm_client(),
        )
        assert result.document.source == str(input_path)

    def test_requests_structured_output_end_to_end(self, tmp_path):
        client = llm_client()
        input_path = write(tmp_path, "lesson.md", FREEFORM)
        extract_to_yaml(input_path, tmp_path / "entries.yaml", "shami",
                        llm_model="a/b", client=client)
        assert client.complete.call_args.kwargs["response_format"] == RESPONSE_FORMAT

    def test_rtf_input_is_converted_before_reaching_the_llm(self, tmp_path):
        """A real regression risk: the LLM must see decoded text, not RTF
        control-word soup."""
        import shutil
        import subprocess

        if shutil.which("textutil") is None:
            pytest.skip("textutil not available on this system")

        # Build a genuine RTF via textutil itself, rather than approximating
        # one by hand -- hand-rolled RTF is easy to get subtly wrong (as a
        # first draft of this fixture did: plain \ansi mangles Arabic, which
        # needs \ansicpg1252-with-cocoa's Unicode escaping to round-trip).
        converted = subprocess.run(
            ["textutil", "-convert", "rtf", "-stdin", "-stdout", "-format", "txt"],
            input=FREEFORM.encode("utf-8"), capture_output=True, check=True,
        )
        rtf_path = tmp_path / "lesson.rtf"
        rtf_path.write_bytes(converted.stdout)

        client = llm_client()
        extract_to_yaml(rtf_path, tmp_path / "entries.yaml", "shami",
                        llm_model="a/b", client=client)

        sent_text = client.complete.call_args[0][0][1]["content"]
        assert r"\rtf1" not in sent_text
        assert "كيفك" in sent_text


class TestOverwriteGuard:
    def test_refuses_to_overwrite_without_force(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"
        out.write_text("language: shami\nentries: []\n", encoding="utf-8")

        with pytest.raises(OutputExistsError):
            extract_to_yaml(input_path, out, "shami")

    def test_original_file_is_untouched_on_refusal(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"
        original = "language: shami\nentries: []\nsentinel: true\n"
        out.write_text(original, encoding="utf-8")

        with pytest.raises(OutputExistsError):
            extract_to_yaml(input_path, out, "shami")

        assert out.read_text(encoding="utf-8") == original

    def test_force_overwrites(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"
        out.write_text("language: shami\nentries: []\n", encoding="utf-8")

        result = extract_to_yaml(input_path, out, "shami", force=True)
        assert len(result.document.entries) == 2

    def test_guard_applies_to_the_llm_path_too(self, tmp_path):
        """Rerunning the LLM path also means paying twice -- the guard is not
        fast-path-specific."""
        input_path = write(tmp_path, "lesson.md", FREEFORM)
        out = tmp_path / "entries.yaml"
        out.write_text("language: shami\nentries: []\n", encoding="utf-8")

        with pytest.raises(OutputExistsError):
            extract_to_yaml(input_path, out, "shami", llm_model="a/b", client=llm_client())


class TestDryRun:
    def test_fast_path_dry_run_writes_nothing(self, tmp_path):
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"

        result = extract_to_yaml(input_path, out, "shami", dry_run=True)

        assert not out.exists()
        assert result.dry_run is True
        assert len(result.document.entries) == 2

    def test_llm_path_dry_run_makes_no_call_and_writes_nothing(self, tmp_path):
        client = MagicMock()
        input_path = write(tmp_path, "lesson.md", FREEFORM)
        out = tmp_path / "entries.yaml"

        result = extract_to_yaml(
            input_path, out, "shami", llm_model="a/b", client=client, dry_run=True
        )

        client.complete.assert_not_called()
        assert not out.exists()
        assert result.used_llm is True

    def test_dry_run_ignores_the_overwrite_guard(self, tmp_path):
        """A preview must never be blocked by -- or need -- force."""
        input_path = write(tmp_path, "lesson.csv", STRUCTURED)
        out = tmp_path / "entries.yaml"
        out.write_text("language: shami\nentries: []\n", encoding="utf-8")

        result = extract_to_yaml(input_path, out, "shami", dry_run=True)
        assert len(result.document.entries) == 2
