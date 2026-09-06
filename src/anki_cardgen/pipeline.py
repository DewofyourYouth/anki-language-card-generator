"""Pipeline 1, step 1: raw lesson file -> entries.yaml.

Ties together the pieces that are each independently testable elsewhere:
``parsing.parse_file`` (fast path), ``extraction.extract_entries`` (LLM
fallback), and ``entries.EntryDocument`` (the review-checkpoint file). This
module's only job is the ordering and the overwrite guard -- it contains no
parsing or extraction logic of its own.
"""

from dataclasses import dataclass
from pathlib import Path

from .entries import EntryDocument
from .extraction import ExtractionResult, extract_entries
from .openrouter import OpenRouterClient
from .parsing import parse_file, read_lesson_text


class OutputExistsError(Exception):
    """Refusing to overwrite an existing entries.yaml without force=True.

    Two reasons this matters: a hand-edited or already-reviewed file
    represents work that a silent rerun would destroy, and on the LLM path a
    rerun also means paying for the extraction a second time.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            f"{path} already exists. Pass force=True to overwrite -- doing so "
            f"discards any review already recorded in it."
        )


@dataclass(frozen=True)
class PipelineResult:
    document: EntryDocument | None
    used_llm: bool
    extraction: ExtractionResult | None = None
    out_path: Path | None = None
    dry_run: bool = False

    def summary(self) -> str:
        if not self.used_llm:
            count = len(self.document.entries) if self.document else 0
            lines = [f"Parsed {count} entr{'y' if count == 1 else 'ies'} (structured, no LLM call)"]
        else:
            lines = [self.extraction.summary()]
        if self.out_path and not self.dry_run:
            lines.append(f"wrote {self.out_path}")
        return " — ".join(lines)


def extract_to_yaml(
    input_path: str | Path,
    out_path: str | Path,
    language: str,
    *,
    llm_model: str = "",
    client: OpenRouterClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> PipelineResult:
    """Turn a raw lesson file into entries.yaml.

    Tries the structured fast path first; only falls back to the one LLM call
    (SPEC section 3) if that path declines the input. The fast path never
    triggers the overwrite guard's cost concern since it is free either way,
    but the guard still applies -- it protects review state, not just money.
    """
    input_path = Path(input_path).expanduser()
    out_path = Path(out_path).expanduser()

    if out_path.exists() and not force and not dry_run:
        raise OutputExistsError(out_path)

    document = parse_file(input_path, language)

    if document is not None:
        result = PipelineResult(document=document, used_llm=False, out_path=out_path, dry_run=dry_run)
        if not dry_run:
            document.dump(out_path)
        return result

    text = read_lesson_text(input_path)
    extraction = extract_entries(
        text, language, model=llm_model, client=client, dry_run=dry_run
    )

    if dry_run:
        return PipelineResult(document=None, used_llm=True, extraction=extraction, dry_run=True)

    document = EntryDocument(
        language=language, source=str(input_path), entries=extraction.entries
    )
    document.dump(out_path)
    return PipelineResult(
        document=document, used_llm=True, extraction=extraction, out_path=out_path
    )
