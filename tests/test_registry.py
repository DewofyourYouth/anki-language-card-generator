"""Slug registry collision rules.

The three rules from SPEC section 5 get one test class each, because each is a
distinct product behaviour and a regression in any one of them corrupts decks
in a different way.
"""

import logging

import pytest

from anki_cardgen.registry import DuplicateContent, SlugCollisionError, SlugRegistry
from anki_cardgen.slugs import text_hash

KIFAK = "كيفك"
KIFAK_VOCALISED = "كَيْفَك"
MNIH = "منيح"


@pytest.fixture
def registry(tmp_path):
    with SlugRegistry(tmp_path / "registry.db") as reg:
        yield reg


class TestSameSlugSameText:
    """No-op: reuse. This is the normal weekly path for an unchanged entry."""

    def test_reserve_twice_is_a_noop(self, registry):
        first = registry.reserve("shami-kifak", "shami", KIFAK)
        second = registry.reserve("shami-kifak", "shami", KIFAK)

        assert first.created is True
        assert second.created is False
        assert second.slug == "shami-kifak"
        assert len(registry) == 1

    def test_assign_twice_reuses_rather_than_suffixing(self, registry):
        first = registry.assign("shami", KIFAK, "kifak")
        second = registry.assign("shami", KIFAK, "kifak")

        assert first.slug == second.slug == "shami-kifak"
        assert second.created is False
        assert len(registry) == 1

    def test_reuse_ignores_incidental_whitespace(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)
        again = registry.reserve("shami-kifak", "shami", f"  {KIFAK}  ")

        assert again.created is False
        assert len(registry) == 1

    def test_reuse_does_not_warn(self, registry):
        registry.assign("shami", KIFAK, "kifak")
        assert registry.assign("shami", KIFAK, "kifak").warnings == ()


class TestSameSlugDifferentText:
    """Hard error. Rebinding a slug silently overwrites an existing note."""

    def test_raises(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)

        with pytest.raises(SlugCollisionError) as exc:
            registry.reserve("shami-kifak", "shami", MNIH)

        assert exc.value.slug == "shami-kifak"
        assert exc.value.existing_text == KIFAK
        assert exc.value.incoming_text == MNIH

    def test_error_message_names_both_texts(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)

        with pytest.raises(SlugCollisionError) as exc:
            registry.reserve("shami-kifak", "shami", MNIH)

        assert KIFAK in str(exc.value)
        assert MNIH in str(exc.value)

    def test_adding_diacritics_is_a_collision(self, registry):
        # Documents the deliberate consequence of preserving harakat: vocalising
        # an existing entry is new content, and must not silently rebind.
        registry.reserve("shami-kifak", "shami", KIFAK)

        with pytest.raises(SlugCollisionError):
            registry.reserve("shami-kifak", "shami", KIFAK_VOCALISED)

    def test_nothing_is_written_on_collision(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)

        with pytest.raises(SlugCollisionError):
            registry.reserve("shami-kifak", "shami", MNIH)

        assert len(registry) == 1
        assert registry.get("shami-kifak").target_text == KIFAK


class TestSameTextDifferentSlug:
    """Loud warning, but the run proceeds."""

    def test_warns_on_reserve(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)
        result = registry.reserve("shami-how-are-you", "shami", KIFAK)

        assert result.created is True
        assert result.warnings == (
            DuplicateContent(
                slug="shami-how-are-you",
                existing_slug="shami-kifak",
                target_text=KIFAK,
            ),
        )

    def test_warns_on_assign_with_different_transliteration(self, registry):
        registry.assign("shami", KIFAK, "kifak")
        result = registry.assign("shami", KIFAK, "kiifak")

        assert result.slug == "shami-kiifak"
        assert [w.existing_slug for w in result.warnings] == ["shami-kifak"]

    def test_warning_is_logged(self, registry, caplog):
        registry.reserve("shami-kifak", "shami", KIFAK)
        with caplog.at_level(logging.WARNING, logger="anki_cardgen.registry"):
            registry.reserve("shami-alt", "shami", KIFAK)

        assert "fork review history" in caplog.text

    def test_run_proceeds_and_both_slugs_persist(self, registry):
        registry.reserve("shami-kifak", "shami", KIFAK)
        registry.reserve("shami-alt", "shami", KIFAK)

        assert len(registry) == 2
        assert {r.slug for r in registry.find_by_content("shami", KIFAK)} == {
            "shami-kifak",
            "shami-alt",
        }

    def test_does_not_warn_across_languages(self, registry):
        registry.reserve("shami-salam", "shami", "سلام")
        result = registry.reserve("fusha-salam", "fusha", "سلام")

        assert result.warnings == ()


class TestSuffixing:
    def test_different_content_with_same_stem_gets_a_suffix(self, registry):
        first = registry.assign("shami", KIFAK, "kifak")
        second = registry.assign("shami", MNIH, "kifak")

        assert first.slug == "shami-kifak"
        assert second.slug == "shami-kifak-2"
        assert second.created is True

    def test_suffixes_increment_past_existing(self, registry):
        registry.assign("shami", "one", "x")
        registry.assign("shami", "two", "x")
        third = registry.assign("shami", "three", "x")

        assert third.slug == "shami-x-3"

    def test_suffixed_slug_is_reused_for_its_own_content(self, registry):
        registry.assign("shami", KIFAK, "kifak")
        registry.assign("shami", MNIH, "kifak")
        again = registry.assign("shami", MNIH, "kifak")

        assert again.slug == "shami-kifak-2"
        assert again.created is False
        assert len(registry) == 2

    def test_assign_never_raises_collision(self, registry):
        registry.assign("shami", KIFAK, "kifak")
        # An occupied base slug is a reason to suffix, not to stop the run.
        assert registry.assign("shami", MNIH, "kifak").slug == "shami-kifak-2"


class TestPersistence:
    """State must survive process boundaries -- the registry is only useful
    across runs, and every rule above is meaningless if it resets."""

    def test_rules_hold_across_reopen(self, tmp_path):
        db = tmp_path / "registry.db"
        with SlugRegistry(db) as reg:
            reg.reserve("shami-kifak", "shami", KIFAK)

        with SlugRegistry(db) as reg:
            assert reg.reserve("shami-kifak", "shami", KIFAK).created is False
            with pytest.raises(SlugCollisionError):
                reg.reserve("shami-kifak", "shami", MNIH)

    def test_creates_parent_directory(self, tmp_path):
        with SlugRegistry(tmp_path / "nested" / "dir" / "registry.db") as reg:
            reg.reserve("shami-kifak", "shami", KIFAK)
        assert (tmp_path / "nested" / "dir" / "registry.db").is_file()

    def test_stores_canonicalised_text(self, registry):
        registry.reserve("shami-kifak", "shami", f"  {KIFAK}  ")
        record = registry.get("shami-kifak")

        assert record.target_text == KIFAK
        assert record.text_hash == text_hash(KIFAK)
        assert record.language == "shami"

    def test_get_returns_none_for_unknown_slug(self, registry):
        assert registry.get("nope") is None
