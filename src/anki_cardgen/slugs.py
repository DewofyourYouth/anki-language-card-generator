"""Canonicalisation, content hashing, and slug derivation.

Pure functions, no I/O. The registry (``registry.py``) is the only thing that
persists any of this; keeping the derivation side-effect free means the same
input always produces the same slug and hash regardless of database state,
which is what makes the GUID stability guarantee testable.
"""

import hashlib
import re
import unicodedata

# Length of the hex digest prefix used when a slug has no transliteration to
# build a readable stem from. Short enough to stay legible in a slug, long
# enough that accidental collisions are not a practical concern for a personal
# deck (16^8 = 4.3e9).
_HASH_STEM_LENGTH = 8

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def canonicalize(text: str) -> str:
    """Return the canonical form of target-language text.

    Normalisation is deliberately conservative: Unicode NFC plus whitespace
    collapsing, and nothing else. In particular Arabic diacritics (harakat) are
    *preserved*, because they change pronunciation and therefore change the TTS
    audio -- text that sounds different is different content, even though it
    looks like the same word.

    The practical consequence: adding harakat to an existing entry makes it new
    content under an existing slug, which the registry treats as a hard error.
    That is intended. Silently rebinding a slug to different audio is the
    failure mode we are protecting against.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def text_hash(text: str) -> str:
    """Return the content hash of ``text`` after canonicalisation."""
    return hashlib.sha256(canonicalize(text).encode("utf-8")).hexdigest()


def ascii_stem(text: str) -> str:
    """Reduce ``text`` to a lowercase ASCII ``[a-z0-9-]`` stem, or ``""``.

    Returns the empty string for input with no ASCII-representable characters
    (e.g. unvocalised Arabic), which is why callers must handle the empty case
    rather than assuming a usable stem always comes back.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_SLUG_CHARS.sub("-", ascii_only).strip("-")


def derive_slug(language: str, target: str, transliteration: str | None = None) -> str:
    """Derive the *base* slug for an entry.

    Prefers the transliteration, which is what makes slugs human-readable
    (``shami-kifak``). Falls back to a hash stem (``shami-a1b2c3d4``) when there
    is no transliteration, or when the transliteration contains nothing
    ASCII-representable -- an ugly slug is recoverable, an empty one is not.

    This is the base only. Uniquifying it against slugs already issued is the
    registry's job, because that requires knowing what has been issued.
    """
    lang_stem = ascii_stem(language)
    if not lang_stem:
        raise ValueError(f"language {language!r} has no ASCII representation")

    stem = ascii_stem(transliteration) if transliteration else ""
    if not stem:
        stem = text_hash(target)[:_HASH_STEM_LENGTH]

    return f"{lang_stem}-{stem}"
