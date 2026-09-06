"""The structured fast path.

If a lesson file is already a clean list of word pairs, Pipeline 1 has no
business calling an LLM to read it -- that would be latency, cost, and a
network failure mode bought for nothing. This module tries to parse the input
locally and returns ``None`` when it cannot, which is the caller's signal to
fall back to LLM extraction.

Detection is deliberately conservative. A false negative costs one LLM call. A
false positive silently mangles a lesson into garbage entries that then have to
be spotted by eye at the review checkpoint, so ambiguity always resolves to
"this is freeform".
"""

import csv
import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .entries import Entry, EntryDocument, SpeakerGender, Variant

#: Extensions that need a conversion step before they are plain text.
#: Anything not listed here (.txt, .md, .csv, .tsv, .yaml, .yml, no suffix) is
#: read as UTF-8 directly.
_RTF_SUFFIXES = {".rtf"}

#: Extensions known to be unreadable without proprietary tooling this project
#: will not depend on. Named explicitly so the error tells the user what to do
#: instead of a raw UnicodeDecodeError from trying to read them as UTF-8.
_UNSUPPORTED_SUFFIXES = {
    ".pages": "Pages",
    ".docx": "Word",
    ".doc": "Word",
    ".odt": "OpenDocument",
}

#: Column names understood in a delimited file's header row.
COLUMNS = {
    "target",
    "transliteration",
    "translit",
    "translation",
    "english",
    "notes",
    "speaker_gender",
    "gender",
    "slug",
    "variant_target",
    "variant_transliteration",
}

_ALIASES = {
    "translit": "transliteration",
    "english": "translation",
    "gender": "speaker_gender",
}

_DELIMITERS = {".csv": ",", ".tsv": "\t"}

#: Minimum share of a header row's cells that must be recognised column names
#: before a suffix-less file is treated as delimited rather than freeform.
_HEADER_CONFIDENCE = 0.75


def read_lesson_text(path: str | Path) -> str:
    """Return the plain-text content of a lesson file, converting if needed.

    Text and delimited formats are read directly. `.rtf` is converted via
    macOS's built-in `textutil` -- a subprocess call rather than a new
    dependency, since it ships with the OS and this is a personal tool that
    runs on one machine. Formats with no practical local parser (`.pages` is a
    zip of Apple's undocumented IWA/protobuf format; there is no maintained
    library for it) are rejected with a message naming the fix, rather than
    failing deep inside a decode error.
    """
    path = Path(path).expanduser()
    suffix = path.suffix.lower()

    if suffix in _UNSUPPORTED_SUFFIXES:
        kind = _UNSUPPORTED_SUFFIXES[suffix]
        raise ValueError(
            f"{path} is a {kind} file, which has no local text extraction "
            f"available. Export it to .txt, .rtf, or .md first."
        )

    if suffix in _RTF_SUFFIXES:
        if shutil.which("textutil") is None:
            raise ValueError(
                f"{path} is an RTF file; converting it requires macOS's "
                f"`textutil`, which was not found on this system. Export the "
                f"lesson to .txt instead."
            )
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            raise ValueError(f"failed to convert {path}: {result.stderr.strip()}")
        return result.stdout

    return path.read_text(encoding="utf-8")


def parse_file(path: str | Path, language: str) -> EntryDocument | None:
    """Parse ``path`` if it is already structured; return ``None`` if freeform.

    Dispatch is by extension, because an explicit `.csv` should fail loudly on
    malformed content rather than being quietly reinterpreted as prose, while a
    `.md` should quietly decline rather than being forced into columns.
    """
    path = Path(path).expanduser()
    text = read_lesson_text(path)
    suffix = path.suffix.lower()
    source = str(path)

    if suffix in {".yaml", ".yml"}:
        document = parse_yaml(text, language, source)
        if document is None:
            raise ValueError(f"{path} is a YAML file but not a valid entries document")
        return document

    if suffix in _DELIMITERS:
        document = parse_delimited(
            text, language, source, delimiter=_DELIMITERS[suffix], require_header=False
        )
        if document is None:
            raise ValueError(
                f"{path} is a {suffix.lstrip('.').upper()} file but its rows are not "
                f"a consistent table of at least 2 columns"
            )
        return document

    # Unknown extension (.md, .txt, no suffix): only claim it when the file
    # announces its own structure. Everything else is freeform.
    return parse_yaml(text, language, source) or parse_delimited(
        text, language, source, delimiter=None, require_header=True
    )


def parse_yaml(text: str, language: str, source: str | None = None) -> EntryDocument | None:
    """Parse an explicit entries document, or a bare list of entry mappings."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None

    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        data.setdefault("language", language)
        data.setdefault("source", source)
        return EntryDocument.from_dict(data)

    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):
        if not all("target" in row for row in data):
            return None
        return EntryDocument.from_dict(
            {"language": language, "source": source, "entries": data}
        )

    return None


def parse_delimited(
    text: str,
    language: str,
    source: str | None = None,
    delimiter: str | None = None,
    require_header: bool = True,
) -> EntryDocument | None:
    """Parse a delimited table of vocabulary rows.

    ``delimiter=None`` sniffs among tab, comma, semicolon and pipe, accepting
    only a delimiter that yields the *same* column count on every row -- prose
    almost never does, which is what keeps freeform notes out of this path.
    """
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return None

    candidates = [delimiter] if delimiter else ["\t", ",", ";", "|"]
    for candidate in candidates:
        rows = _read_rows(lines, candidate)
        if rows is None:
            continue

        header, body = _split_header(rows)
        if header is None:
            if require_header:
                continue
            header = _positional_header(len(rows[0]))
            if header is None:
                continue
            body = rows

        if not body:
            continue

        entries = [_entry_from_row(header, row) for row in body]
        return EntryDocument(
            language=language, source=source, entries=tuple(entries)
        )

    return None


# -- internals ---------------------------------------------------------------


def _read_rows(lines: list[str], delimiter: str) -> list[list[str]] | None:
    """Return rows if every line yields the same 2..8 non-empty fields."""
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = [[cell.strip() for cell in row] for row in reader]
    if not rows:
        return None

    width = len(rows[0])
    if not 2 <= width <= 8:
        return None
    if any(len(row) != width for row in rows):
        return None
    # A table with empty cells is far more likely to be prose that happened to
    # contain the delimiter than an actual vocabulary list.
    if any(not cell for row in rows for cell in row):
        return None
    return rows


def _split_header(rows: list[list[str]]) -> tuple[list[str] | None, list[list[str]]]:
    """Detect and normalise a header row, if the first row looks like one."""
    first = [cell.strip().lower().replace(" ", "_") for cell in rows[0]]
    recognised = sum(1 for cell in first if cell in COLUMNS)
    if recognised / len(first) < _HEADER_CONFIDENCE:
        return None, rows
    if "target" not in first:
        return None, rows
    return [_ALIASES.get(cell, cell) for cell in first], rows[1:]


def _positional_header(width: int) -> list[str] | None:
    """Column meanings for a headerless table, by width."""
    if width == 2:
        return ["target", "translation"]
    if width == 3:
        return ["target", "transliteration", "translation"]
    return None


def _entry_from_row(header: list[str], row: list[str]) -> Entry:
    raw: dict[str, Any] = dict(zip(header, row))

    variant = None
    variant_target = raw.pop("variant_target", None)
    variant_translit = raw.pop("variant_transliteration", None)
    if variant_target:
        variant = Variant(target=variant_target, transliteration=variant_translit or None)

    gender_raw = raw.pop("speaker_gender", "any") or "any"
    try:
        gender = SpeakerGender(gender_raw.lower())
    except ValueError:
        raise ValueError(
            f"row {row!r} has invalid speaker_gender {gender_raw!r}; "
            f"expected male, female, or any"
        ) from None

    if "translation" not in raw:
        raise ValueError(f"row {row!r} has no translation column")

    return Entry(
        target=raw["target"],
        translation=raw["translation"],
        transliteration=raw.get("transliteration") or None,
        notes=raw.get("notes") or None,
        speaker_gender=gender,
        variant=variant,
        slug=raw.get("slug") or None,
    )
