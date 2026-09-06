"""Slug derivation and canonicalisation."""

import pytest

from anki_cardgen.slugs import ascii_stem, canonicalize, derive_slug, text_hash

KIFAK = "كيفك"  # كيفك  (unvocalised)
KIFAK_VOCALISED = "كَيْفَك"  # كَيْفَك


class TestCanonicalize:
    def test_collapses_whitespace(self):
        assert canonicalize("  how   are\tyou \n") == "how are you"

    def test_nfc_normalises_equivalent_forms(self):
        # Same character, composed vs decomposed. These must hash identically or
        # a copy-paste from a different source forks the slug.
        assert canonicalize("é") == canonicalize("é")

    def test_preserves_diacritics(self):
        # Harakat change pronunciation, so they change the TTS audio, so they
        # are different content. See registry rules.
        assert canonicalize(KIFAK) != canonicalize(KIFAK_VOCALISED)


class TestTextHash:
    def test_stable_across_calls(self):
        assert text_hash(KIFAK) == text_hash(KIFAK)

    def test_ignores_surrounding_whitespace(self):
        assert text_hash(f"  {KIFAK} ") == text_hash(KIFAK)

    def test_differs_for_different_content(self):
        assert text_hash(KIFAK) != text_hash(KIFAK_VOCALISED)


class TestAsciiStem:
    def test_lowercases_and_hyphenates(self):
        assert ascii_stem("How Are You") == "how-are-you"

    def test_strips_accents(self):
        assert ascii_stem("café") == "cafe"

    def test_collapses_punctuation_runs(self):
        assert ascii_stem("well... maybe?") == "well-maybe"

    def test_returns_empty_for_non_ascii_script(self):
        # This is the case derive_slug's hash fallback exists for.
        assert ascii_stem(KIFAK) == ""


class TestDeriveSlug:
    def test_prefers_transliteration(self):
        assert derive_slug("shami", KIFAK, "kifak") == "shami-kifak"

    def test_falls_back_to_hash_stem_without_transliteration(self):
        slug = derive_slug("shami", KIFAK)
        assert slug == f"shami-{text_hash(KIFAK)[:8]}"

    def test_falls_back_when_transliteration_has_no_ascii(self):
        assert derive_slug("shami", KIFAK, KIFAK) == f"shami-{text_hash(KIFAK)[:8]}"

    def test_deterministic(self):
        assert derive_slug("shami", KIFAK) == derive_slug("shami", KIFAK)

    def test_rejects_language_with_no_ascii_form(self):
        with pytest.raises(ValueError):
            derive_slug("شامي", KIFAK, "kifak")
