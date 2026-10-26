"""Text conventions shared by training, scoring and serving.

The source dataset's review text uses exactly 63 characters: ASCII letters,
digits and the space (the publisher removed punctuation; measured over all
515,212 reviews). :func:`normalize_text` maps any input onto that alphabet,
so text typed into the API looks like the text the model was trained on:

* HTML entities are unescaped (``&#39;`` becomes an apostrophe),
* accents are folded to ASCII (``Café`` becomes ``Cafe``),
* every other character becomes a space (``didn't`` becomes ``didn t``,
  exactly as in the source),
* runs of whitespace collapse to one space; case is kept.

The classifier always sees a normalised part prefixed with its marker, e.g.
``"Liked: Great location"``; :func:`format_input` is the single place that
builds that string. This module has no heavy dependencies so the API can
import it.
"""

import html
import re
import unicodedata

PART_MARKERS: dict[str, str] = {"liked": "Liked", "disliked": "Disliked"}
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def normalize_text(text: str) -> str:
    """Map text onto the source alphabet ``[A-Za-z0-9 ]``; idempotent."""
    unescaped = html.unescape(text)
    folded = (
        unicodedata.normalize("NFKD", unescaped)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return _NOT_ALNUM.sub(" ", folded).strip()


def format_input(text: str, part: str) -> str:
    """Normalise a review part and prefix its marker, e.g. ``"Liked: ..."``.

    Raises:
        ValueError: If ``part`` is not ``liked`` or ``disliked``.
    """
    marker = PART_MARKERS.get(part)
    if marker is None:
        raise ValueError(f"part must be 'liked' or 'disliked', got {part!r}")
    return f"{marker}: {normalize_text(text)}"
