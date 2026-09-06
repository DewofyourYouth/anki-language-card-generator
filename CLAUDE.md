# CLAUDE.md

Standing instructions for this repo. Read before doing anything.

## What this is

`anki-language-card-generator` — turns raw vocabulary lists or messy lesson
notes into a finished Anki deck: cards, TTS audio, and (via a separate
interactive pipeline) mnemonic images.

Built for Levantine Arabic study but the language must be configurable, not
hardcoded.

Full requirements live in `SPEC.md`. Read it before the first task. When
`SPEC.md` and this file disagree, this file wins.

## How I work

- **Teach and execute.** After building a feature, explain what it does and
  the concept + data flow behind it. Don't just hand me working code.
- **Plan before sweeping edits.** Show files touched and order, get sign-off,
  then implement. Applies to anything crossing more than two files.
- **Incremental, testable commits.** Never a big-bang rewrite. Don't break
  existing tests.
- **No fluff.** Lead with the answer. Skip validating preambles. If something
  can't be done or is a bad idea, say so plainly.
- **Flag new dependencies** and why, before adding them. This stack should
  stay light.
- **Prefer local computation over API calls.** Every avoidable network call is
  latency, cost, and a failure mode.

## Invariants — do not violate

These are the product. If one seems inefficient and you're tempted to
optimize it away, flag it and ask instead.

1. **Never auto-select an image.** Pipeline 2 proposes candidates; the human
   chooses. A wrong image gets permanently attached to a card seen hundreds of
   times. Never add a "just pick the best one" mode, for any reason.

2. **Note GUIDs derive from the slug alone.** This is what makes re-import
   update existing notes instead of duplicating them, preserving review
   history. Any change here silently corrupts a deck. Regression-test it.

3. **The user's Anki collection is read-only.** Copy `collection.anki2` to a
   temp path and open the copy, or use AnkiConnect. Never open the live
   collection read-write. Never write into `collection.media` directly —
   embed media in the `.apkg` so Anki imports it.

4. **The mnemonic image never appears on a card front.** Referent images go
   with the translation; mnemonic hooks go on backs only. A hook on a prompt
   face gives away the answer. Test that no `qfmt` contains `{{Mnemonic}}`.

5. **Personal reference photos never combine with sexual, violent, gory, or
   threatening attribute weights.** Not configurable. No override flag.

6. **Attributes are dials on the scene, never words in the prompt.** Abstract
   labels ("sexy", "violent", "taboo") produce weak images and inconsistent
   refusals. Concrete scene description only. See `SPEC.md` for the worked
   table.

## Architecture rules

- **Two pipelines, always separate.** Pipeline 1 (cards + audio) runs
  unattended and is the weekly path. Pipeline 2 (images) is interactive,
  expensive, and runs on selected cards only. Never fold them together.
- **Slug registry is load-bearing.** SQLite, every slug ever issued. Hard
  error on a slug reused for different text; loud warning when new input
  duplicates existing content under a different slug. Both failure modes have
  bitten me in production — treat this as the most important component in the
  repo, not plumbing.
- **Image backends behind a protocol.** OpenRouter/Seedream today; a local
  SDXL/Flux endpoint must be swappable without touching pipeline logic.
- **Human-review checkpoints are features, not v1 shortcuts.** The
  `entries.yaml` review step and the image selection step both stay.

## Stack

- Python 3.14, `pyproject.toml`, `uv`. I use pyenv.
- OpenRouter for LLM *and* image generation — one key, one client.
- ElevenLabs for TTS.
- Keys and voice IDs from env only: `OPENROUTER_API_KEY`,
  `ELEVENLABS_API_KEY`, `ELEVENLABS_MALE_VOICE_ID`,
  `ELEVENLABS_FEMALE_VOICE_ID`. Never committed, never logged. Ship
  `.env.example`.
- Config in `config.yaml`: language, deck name, model/deck IDs, model slugs,
  style directives.
- Two TTS voices, one male and one female, split ~50-50 across the deck by
  slug-hash parity. Speaker-marked entries carry both gender variants so the
  text can follow the assigned voice.

**Model names churn.** Verify current OpenRouter model slugs against
openrouter.ai before relying on any default. Do not trust training data for
model identifiers.

## Testing

- All API calls mocked. No test spends money or needs a real key.
- Registry collision logic tested hard.
- GUID stability across separate builds — regression test.
- `{{Mnemonic}}` absent from every `qfmt` — regression test.
- apkg validation by unzipping and asserting against the sqlite, not by
  trusting the writer.

## Cost controls

- `--dry-run` on every command that spends money.
- Per-run image cap, default 10.
- TTS cached by slug + text hash; never re-synthesize unchanged audio.
- Running cost printed as it goes.

## PR conventions

- One PR per deliverable, not per file.
- PR description: what it does, what it deliberately doesn't do yet, and any
  decision I should review.
- Call out anything you had to guess about, explicitly. Don't bury
  assumptions in code comments.
