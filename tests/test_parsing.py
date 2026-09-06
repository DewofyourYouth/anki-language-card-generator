"""Structured fast path detection.

The asymmetry under test: declining a structured file costs one LLM call,
while claiming a freeform file mangles a lesson into garbage entries. Every
ambiguous case must resolve to "freeform".
"""

import shutil
import subprocess

import pytest

from anki_cardgen.entries import SpeakerGender
from anki_cardgen.parsing import parse_delimited, parse_file, parse_yaml, read_lesson_text

HAS_TEXTUTIL = shutil.which("textutil") is not None

FREEFORM = """\
# Lesson 3 notes

Today we covered greetings. كيفك means "how are you", said to a man.
For a woman you'd say كيفِك instead, which trips me up constantly.

Teacher mentioned منيح (mnih) = good, fine. Also came up in the phrase
"كيف الحال؟" which is more formal, apparently used more in writing.
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestFreeformDeclined:
    def test_prose_notes_are_not_claimed(self, tmp_path):
        path = write(tmp_path, "lesson.md", FREEFORM)
        assert parse_file(path, "shami") is None

    def test_prose_with_commas_is_not_claimed(self, tmp_path):
        text = "We learned greetings, numbers, and food words.\nIt was hard, but fun.\n"
        assert parse_file(write(tmp_path, "n.md", text), "shami") is None

    def test_headerless_table_in_md_is_not_claimed(self, tmp_path):
        """Consistent columns but no header: too close to prose to risk it in a
        file that never announced itself as data."""
        text = "كيفك,how are you\nمنيح,good\n"
        assert parse_file(write(tmp_path, "n.md", text), "shami") is None

    def test_empty_file_is_not_claimed(self, tmp_path):
        assert parse_file(write(tmp_path, "n.md", "\n\n"), "shami") is None


class TestDelimited:
    def test_headerless_csv_two_columns(self, tmp_path):
        path = write(tmp_path, "v.csv", "كيفك,how are you\nمنيح,good\n")
        doc = parse_file(path, "shami")

        assert [e.target for e in doc.entries] == ["كيفك", "منيح"]
        assert doc.entries[0].translation == "how are you"
        assert doc.language == "shami"

    def test_headerless_csv_three_columns_is_target_translit_translation(self, tmp_path):
        path = write(tmp_path, "v.csv", "كيفك,kifak,how are you\n")
        entry = parse_file(path, "shami").entries[0]

        assert entry.transliteration == "kifak"
        assert entry.translation == "how are you"

    def test_tsv(self, tmp_path):
        path = write(tmp_path, "v.tsv", "كيفك\tkifak\thow are you\n")
        assert parse_file(path, "shami").entries[0].transliteration == "kifak"

    def test_header_row_is_honoured(self, tmp_path):
        text = "translation,target\nhow are you,كيفك\n"
        entry = parse_file(write(tmp_path, "v.csv", text), "shami").entries[0]

        assert entry.target == "كيفك"
        assert entry.translation == "how are you"

    def test_header_aliases(self, tmp_path):
        text = "target,translit,english\nكيفك,kifak,how are you\n"
        entry = parse_file(write(tmp_path, "v.csv", text), "shami").entries[0]

        assert entry.transliteration == "kifak"
        assert entry.translation == "how are you"

    def test_header_in_md_is_claimed(self, tmp_path):
        """A file that names its own columns has announced it is data."""
        text = "target,transliteration,translation\nكيفك,kifak,how are you\n"
        doc = parse_file(write(tmp_path, "v.md", text), "shami")
        assert doc is not None and doc.entries[0].target == "كيفك"

    def test_gender_and_variant_columns(self, tmp_path):
        text = (
            "target,translation,speaker_gender,variant_target\n"
            "أنا جوعان,I'm hungry,male,أنا جوعانة\n"
        )
        entry = parse_file(write(tmp_path, "v.csv", text), "shami").entries[0]

        assert entry.speaker_gender is SpeakerGender.MALE
        assert entry.variant.target == "أنا جوعانة"

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        text = "# lesson 3\n\nكيفك,how are you\n\nمنيح,good\n"
        assert len(parse_file(write(tmp_path, "v.csv", text), "shami").entries) == 2

    def test_ragged_csv_raises_rather_than_guessing(self, tmp_path):
        """An explicit .csv that will not parse is a mistake worth surfacing,
        not something to silently reroute through an LLM."""
        with pytest.raises(ValueError, match="consistent table"):
            parse_file(write(tmp_path, "v.csv", "a,b\nc\nd,e,f\n"), "shami")

    def test_rows_with_empty_cells_rejected(self):
        assert parse_delimited("كيفك,\nمنيح,good\n", "shami", delimiter=",",
                               require_header=False) is None

    def test_sniffs_semicolon(self):
        doc = parse_delimited("كيفك;how are you\n", "shami", require_header=False)
        assert doc.entries[0].translation == "how are you"


class TestYaml:
    def test_full_entries_document(self, tmp_path):
        text = (
            "language: shami\n"
            "entries:\n"
            "  - target: كيفك\n"
            "    translation: how are you\n"
        )
        doc = parse_file(write(tmp_path, "v.yaml", text), "shami")
        assert doc.entries[0].target == "كيفك"

    def test_bare_list_of_mappings(self, tmp_path):
        text = "- target: كيفك\n  translation: how are you\n"
        doc = parse_file(write(tmp_path, "v.yml", text), "shami")
        assert doc.entries[0].target == "كيفك"

    def test_language_defaults_from_argument(self, tmp_path):
        text = "- target: كيفك\n  translation: how are you\n"
        assert parse_file(write(tmp_path, "v.yaml", text), "fusha").language == "fusha"

    def test_explicit_language_wins(self, tmp_path):
        text = "language: shami\nentries:\n  - target: a\n    translation: b\n"
        assert parse_file(write(tmp_path, "v.yaml", text), "fusha").language == "shami"

    def test_invalid_yaml_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not a valid entries document"):
            parse_file(write(tmp_path, "v.yaml", "just a string\n"), "shami")

    def test_prose_is_not_mistaken_for_yaml(self):
        assert parse_yaml(FREEFORM, "shami") is None

    def test_source_is_recorded(self, tmp_path):
        path = write(tmp_path, "v.csv", "كيفك,how are you\n")
        assert parse_file(path, "shami").source == str(path)


class TestReadLessonText:
    def test_plain_text_is_read_directly(self, tmp_path):
        path = write(tmp_path, "v.txt", "hello كيفك")
        assert read_lesson_text(path) == "hello كيفك"

    def test_delimited_extensions_are_read_directly(self, tmp_path):
        path = write(tmp_path, "v.csv", "a,b\n")
        assert read_lesson_text(path) == "a,b\n"

    def test_pages_is_rejected_with_an_actionable_message(self, tmp_path):
        path = tmp_path / "v.pages"
        path.write_bytes(b"PK\x03\x04")  # a zip signature is enough; never reached
        with pytest.raises(ValueError, match="Export it to .txt, .rtf, or .md"):
            read_lesson_text(path)

    def test_docx_is_rejected_too(self, tmp_path):
        path = tmp_path / "v.docx"
        path.write_bytes(b"PK\x03\x04")
        with pytest.raises(ValueError, match="Word file"):
            read_lesson_text(path)

    @pytest.mark.skipif(not HAS_TEXTUTIL, reason="textutil not available on this system")
    def test_rtf_is_converted_to_plain_text(self, tmp_path):
        converted = subprocess.run(
            ["textutil", "-convert", "rtf", "-stdin", "-stdout", "-format", "txt"],
            input="hello كيفك".encode("utf-8"), capture_output=True, check=True,
        )
        path = tmp_path / "v.rtf"
        path.write_bytes(converted.stdout)

        text = read_lesson_text(path)
        assert "hello" in text
        assert "كيفك" in text
        assert r"\rtf1" not in text

    def test_rtf_without_textutil_available_gives_actionable_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        path = write(tmp_path, "v.rtf", "irrelevant -- fails before being read as RTF")
        with pytest.raises(ValueError, match="Export the lesson to .txt"):
            read_lesson_text(path)

    def test_rtf_conversion_failure_surfaces_stderr(self, tmp_path, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")
        monkeypatch.setattr("subprocess.run", fake_run)

        path = write(tmp_path, "v.rtf", "irrelevant")
        with pytest.raises(ValueError, match="boom"):
            read_lesson_text(path)

    def test_parse_file_uses_read_lesson_text_for_rtf(self, tmp_path):
        """The fast path must see decoded text too, not just extraction. Like
        .md/.txt, .rtf doesn't self-announce as structured, so (matching
        test_headerless_table_in_md_is_not_claimed) it only gets claimed with
        an explicit header row."""
        if not HAS_TEXTUTIL:
            pytest.skip("textutil not available on this system")
        converted = subprocess.run(
            ["textutil", "-convert", "rtf", "-stdin", "-stdout", "-format", "txt"],
            input="target,translation\nكيفك,how are you\n".encode("utf-8"),
            capture_output=True, check=True,
        )
        path = tmp_path / "v.rtf"
        path.write_bytes(converted.stdout)

        doc = parse_file(path, "shami")
        assert doc is not None
        assert doc.entries[0].target == "كيفك"

    def test_rtf_without_a_header_is_left_to_the_llm(self, tmp_path):
        """The realistic case: real lesson RTF is prose, not a table, and must
        fall through to extraction rather than being force-parsed."""
        if not HAS_TEXTUTIL:
            pytest.skip("textutil not available on this system")
        converted = subprocess.run(
            ["textutil", "-convert", "rtf", "-stdin", "-stdout", "-format", "txt"],
            input="كيفك,how are you\n".encode("utf-8"), capture_output=True, check=True,
        )
        path = tmp_path / "v.rtf"
        path.write_bytes(converted.stdout)

        assert parse_file(path, "shami") is None
