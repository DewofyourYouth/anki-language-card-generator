# SPEC.md

Requirements for `anki-language-card-generator`. This is a first draft for
review — sections marked **[GUESS]** are assumptions made to produce a
concrete, buildable spec and should be confirmed or corrected before any
code lands. See `CLAUDE.md` for standing rules; where the two conflict,
`CLAUDE.md` wins.

## 1. Purpose

Turn raw vocabulary lists or messy lesson notes for a language under active
study (initially Levantine Arabic — "Shami") into a finished Anki deck:
cards with target-language text, translation, and TTS audio, plus — via a
separate, interactive, human-gated pipeline — a mnemonic image per card.

The language, deck, and voice are config, not hardcoded, so the same tool
works for a different language later without code changes.

## 2. Two pipelines

### Pipeline 1 — cards + audio (unattended, weekly)

```
raw input (vocab list / lesson notes)
  → parse into structured entries
  → entries.yaml (human review checkpoint)
  → slug assignment (slug registry)
  → Anki note construction
  → TTS synthesis (ElevenLabs, cached by slug+text hash)
  → .apkg build (embeds audio media)
```

Runs unattended once entries.yaml is approved. This is the path run weekly
against new lesson notes.

### Pipeline 2 — mnemonic images (interactive, expensive)

```
selected cards (subset, not the whole deck)
  → prompt built from concrete scene description (not abstract attribute words)
  → N candidate images generated (OpenRouter/Seedream today, swappable backend)
  → human reviews candidates, picks one (or none) — never auto-selected
  → chosen image attached to the note's Mnemonic field, back-only
```

Runs only on cards the user selects, never on the whole deck automatically —
this is the expensive, budget-capped path.

These two pipelines never share a code path beyond the slug registry and
note-writing layer; a run of one must never implicitly trigger the other.

## 3. Input format

**[GUESS]** Input is a single Markdown or plain-text file per lesson,
freeform (vocab lines, notes, example sentences mixed together). Pipeline 1
uses an LLM call (via OpenRouter) to extract structured vocabulary entries
from this freeform text — this is the one LLM call in Pipeline 1, and its
output is what populates `entries.yaml` for human review before anything is
written. If instead the input is expected to already be a clean structured
list (e.g. CSV/YAML of word pairs), the parse step becomes trivial string
splitting and there is no LLM call in Pipeline 1 at all. **This changes the
dependency shape of Pipeline 1 significantly — needs confirmation.**

## 4. `entries.yaml` — review checkpoint

One YAML document per pipeline-1 run, human-editable before continuing.
**[GUESS]** shape:

```yaml
language: shami
source: lessons/2026-09-05-notes.md
entries:
  - target: "كيفك"
    transliteration: "kifak"
    translation: "how are you (m.)"
    notes: "informal, masculine addressee"
    slug: null          # filled in by the registry step, or by hand to force reuse
  - target: "منيح"
    transliteration: "mnih"
    translation: "good / fine"
    slug: null
```

The tool parses input → writes this file → **stops**. A separate command
(e.g. `generate-cards entries.yaml`) resumes from the approved file. Editing
`target`/`translation` text before resuming is expected and must re-run
slug-collision detection (see §5) rather than trusting stale slugs.

## 5. Slug registry

SQLite database (**[GUESS]** path `~/.anki_card_gen/registry.db`, override
via `config.yaml`), one row per slug ever issued:

| column       | type | notes                                   |
|--------------|------|------------------------------------------|
| slug         | TEXT PRIMARY KEY | stable identifier, drives the GUID |
| language     | TEXT | e.g. `shami`                             |
| text_hash    | TEXT | hash of the canonical target-language text the slug was issued for |
| target_text  | TEXT | for human-readable collision messages    |
| created_at   | TEXT | ISO timestamp                            |

Rules (both are product invariants per `CLAUDE.md`, not suggestions):

- **Same slug, different text** → hard error, stop the run. A slug is only
  ever bound to one piece of content.
- **Same text (by hash), different/new slug** → loud warning, do not
  silently proceed — this usually means the same vocab item is being
  re-entered under a new name and will fork review history.
- **Same slug, same text** → no-op, reuse.

Slug format **[GUESS]**: `{language}-{normalized-transliteration-or-slugified-text}`,
lowercased, ASCII, hyphenated (e.g. `shami-kifak`). Collisions on the
human-readable part get a numeric suffix (`shami-kifak-2`) — but note that
does *not* by itself resolve the hard-error case above; a numeric suffix is
a new slug, so reusing it for the *same* text is still governed by the
same-text rule.

## 6. Note GUID derivation

`guid = deterministic_hash(slug)` — the slug alone, nothing else (not
text, not language, not timestamp). This is what makes re-running Pipeline 1
over edited/re-approved entries.yaml update the existing Anki note in place
instead of duplicating it. Regression test: same slug → same GUID across two
separate process runs; different slug → different GUID even if all other
fields are identical.

## 7. Anki note model

**[GUESS]** Fields:

- `Target` — target-language text
- `Transliteration`
- `Translation`
- `Audio` — `[sound:...]` reference, embedded media
- `Mnemonic` — image reference, **back template only**

Card template invariant (tested): no `qfmt` (question/front template) may
contain `{{Mnemonic}}`. The mnemonic hook is a memory aid for recall, not
part of the prompt — putting it on the front gives the answer away.

Deck/model IDs come from `config.yaml` so re-running against the same deck
updates it rather than creating a parallel one in Anki.

## 8. `.apkg` construction

Built via `genanki` **[GUESS — flag as new dependency: lightweight, pure
Python, no network, matches "prefer local computation"]**. Audio files are
embedded as media in the `.apkg`, never written into the user's
`collection.media` directly (invariant #3). If the user wants to check
against an existing collection (e.g. for slug/GUID sanity), the tool copies
`collection.anki2` to a temp path and inspects the copy — never opens the
live file read-write. AnkiConnect is an alternative for that read path if
available, but is not required.

## 9. TTS (ElevenLabs)

- Cached by `(slug, text_hash)` — unchanged audio is never re-synthesized,
  even across separate runs.
- Voice ID from `config.yaml`, per-language.
- Cost printed running-total as synthesis happens; `--dry-run` shows what
  *would* be synthesized (and its cost) without calling the API.

## 10. Pipeline 2 — mnemonic image generation

### Attribute model

Attributes are **dials on a concrete scene description**, never abstract
words injected into the prompt. Example table **[GUESS — worked example,
confirm the actual dial set and ranges]**:

| Dial              | Range | Effect on scene description |
|-------------------|-------|------------------------------|
| `absurdity`       | 0–3   | 0 = literal depiction; 3 = surreal/exaggerated juxtaposition |
| `personal_ref`    | 0/1   | 1 = incorporate the user's reference photo as a character in the scene |
| `humor`           | 0–3   | 0 = neutral; 3 = slapstick/exaggerated expression |
| `vividness`       | 0–3   | detail density / color saturation of the described scene |

Invariant #5 (safety): whenever `personal_ref = 1`, the scene-description
builder must refuse to combine it with sexual, violent, gory, or
threatening content — this check is unconditional code, not a config flag,
and has no override.

### Candidate generation & selection

- Backend is a protocol/interface; today's implementation calls
  OpenRouter/Seedream. **[GUESS]** interface shape:

  ```python
  class ImageBackend(Protocol):
      def generate(self, prompt: str, n: int) -> list[ImageCandidate]: ...
  ```

  so a local SDXL/Flux endpoint can be dropped in later without touching
  pipeline logic.
- Default candidate count and per-run image cap: 10 (cost control, per
  `CLAUDE.md`), configurable.
- Candidates are presented to the human (**[GUESS] mechanism — CLI image
  preview + numbered selection, vs. a small local web UI — needs a
  decision**); the tool writes the chosen image into the note's `Mnemonic`
  field. No code path may pick automatically, including "pick the highest
  scoring" — there is no scoring.

## 11. Config (`config.yaml`)

**[GUESS]** shape:

```yaml
language: shami
deck_name: "Shami Vocabulary"
anki_model_id: 1607392319   # fixed per deck, do not regenerate
anki_deck_id: 2059400110
voice_id: "..."              # ElevenLabs voice id
llm_model: "..."             # OpenRouter slug — verify against openrouter.ai before use
image_model: "..."           # OpenRouter slug for image generation
style_directives: "..."      # freeform scene-style guidance for image prompts
image_cap_per_run: 10
registry_db_path: "~/.anki_card_gen/registry.db"
```

## 12. CLI surface

**[GUESS]** — needs confirmation of exact command names/flags:

```
anki-cardgen extract <input.md> -o entries.yaml     # Pipeline 1, step 1
anki-cardgen build entries.yaml -o deck.apkg         # Pipeline 1, step 2 (--dry-run supported)
anki-cardgen mnemonic <slug...> [--n 10] [--dry-run] # Pipeline 2
```

Every command that spends money (`build` when TTS is needed, `mnemonic`)
supports `--dry-run`.

## 13. Testing requirements

- All OpenRouter/ElevenLabs calls mocked; no test spends money or requires
  a real key.
- Slug registry collision logic: same-slug-different-text (hard error),
  same-text-different-slug (warning), same-slug-same-text (no-op) — each a
  separate test.
- GUID stability: two independent builds from the same slug produce the
  same GUID; changing only the target text (same slug) does not change the
  GUID (and is itself a same-slug-different-text collision, tested above).
- Card template regression test: assert `{{Mnemonic}}` is absent from every
  `qfmt` across all note types shipped.
- `.apkg` validation: unzip the built package and assert against the
  embedded SQLite directly — never trust the writer library's return value
  as proof of correctness.

## 14. Open questions for review

1. §3 — is input freeform notes (needs an LLM extraction call) or already
   structured (no LLM call in Pipeline 1)?
2. §7 — confirm the note field set (is transliteration wanted as a visible
   field, or folded into `Target`?).
3. §8 — `genanki` as the `.apkg` writer: approve as a new dependency?
4. §10 — human image-selection UX: CLI-only (numbered list + open in
   default viewer) or a minimal local web page?
5. §11/§12 — confirm config keys and CLI command names before they become
   load-bearing (renaming a shipped CLI command later is a breaking change
   for a personal weekly-use tool, so worth getting right once).
