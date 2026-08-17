"""A board key from something a person typed.

Pure, synchronous, and importing nothing — which is what lets it live in
`domain/`, where both the things that need it can reach it. Authoring mints card
keys; the compiler mints the spec's slug, and the layering rule forbids the
compiler from importing authoring. A key is a value of a domain type, so the
rule for making a legal one belongs beside the type.

A key reaches further than a slug usually does. It becomes a generated Python
filename and a spec entry, and every thread anchor, assertion and eval failure
references it with **no foreign key**, deliberately, so a card can be deleted and
re-created during review without orphaning its conversation. That is also why a
key is minted once, at creation, and never regenerated when the name changes:
renumbering would leave every comment pinned to nothing.

Two rules the implementation exists to keep:

**Whatever comes out matches ``board_key``.** It is written into a column with a
domain check, so an illegal key is a failed insert rather than an ugly name.
That is asserted over arbitrary input rather than reasoned about case by case.

**A key is a word.** Somebody reads it in a stack trace, so a name that survives
nothing at all falls back to the card's own type — `check`, `event` — never
`p_7`.
"""

import re
import unicodedata
from collections.abc import Collection

# `^[a-z][a-z0-9_]{1,63}$` — a letter first, two to sixty-four characters.
CEILING = 64
FLOOR = 2

_UNUSABLE = re.compile(r"[^a-z0-9]+")
_NOT_A_LETTER_FIRST = re.compile(r"^[^a-z]+")


def key_for(name: str, taken: Collection[str], *, fallback: str) -> str:
    """A legal, unused board key for a card or an edge someone just named.

    Args:
        name: whatever they typed. May be empty, punctuation, or another script.
        taken: the keys already in use in this namespace. Primitive keys and
            edge keys are separate namespaces — the schema treats them so, and
            anchors are qualified (`primitive:` / `edge:`), so there is nothing
            to disambiguate between them.
        fallback: what to call it when the name survives to nothing. The card's
            own type word, so the result is still readable.

    Returns:
        A key matching ``board_key`` that is not in ``taken``.
    """
    stem = _stem(name) or _stem(fallback) or "card"
    if stem not in taken:
        return stem

    # From two, never one: the first card of a name is unsuffixed, so `_1` would
    # imply it was numbered too and that a `..._1` exists somewhere.
    for n in range(2, len(taken) + 3):
        suffix = f"_{n}"
        candidate = _trimmed(stem, CEILING - len(suffix)) + suffix
        if candidate not in taken:
            return candidate
    raise AssertionError("unreachable: the range exceeds the number of taken keys")


def _stem(name: str) -> str:
    """The name reduced to something the pattern accepts, or empty if nothing survives."""
    # Decompose, then drop the combining marks — so "Rückgabe" keeps its `u`
    # rather than losing the letter to the catch-all below.
    folded = unicodedata.normalize("NFKD", name).casefold()
    plain = "".join(c for c in folded if not unicodedata.combining(c))

    slug = _UNUSABLE.sub("_", plain).strip("_")
    slug = _NOT_A_LETTER_FIRST.sub("", slug)
    if len(slug) < FLOOR:
        return ""
    return _trimmed(slug, CEILING)


def _trimmed(slug: str, limit: int) -> str:
    """Cut to ``limit``, preferring a word boundary, never ending in an underscore.

    Cutting mid-word is legal and unreadable, so a nearby underscore wins — but
    only a nearby one, since backing up to the first underscore in a long name
    would throw away most of it.
    """
    if len(slug) <= limit:
        return slug.rstrip("_")

    cut = slug[:limit]
    boundary = cut.rfind("_")
    if boundary >= limit - 12 and boundary >= FLOOR:
        cut = cut[:boundary]
    return cut.rstrip("_")


__all__ = ["key_for"]
