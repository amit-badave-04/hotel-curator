"""Spot review parts that probably name a person, so they are not quoted.

Quotes are redacted with spaCy NER (``evidence.Redactor``), but the corpus
has no punctuation and often no capitals, and the small English model misses
many staff names ("staff especially nicoletta", "receptionist called Laura
Aguirrebengoa"). Masking is therefore only the second layer: quote selection
first skips any part in which a person-introducing word is followed by a word
that is rare in the corpus, and any part about staff that contains a rare
capitalised word ("Andreea was so helpful", "reception Nicola").

The rarity cut-off is a share of the parts the guard is built from: the
pipeline builds it from the eligible reviews' parts, the ones that can be
quoted (851,834 of the corpus's 865,732). In those, staff names appear in at
most about 200 parts each (e.g. "maria" in 195), while the ordinary words
that follow "especially" or "receptionist" appear in thousands (e.g.
"courteous" in 2,706). A rare ordinary word after a trigger also skips the
part; that costs one candidate, never a wrong quote.
"""

import math
from collections import Counter
from collections.abc import Iterable

from spacy.lang.en.stop_words import STOP_WORDS

COMMON_WORD_MIN_SHARE = 0.0005
"""A word is ordinary if at least this share of review parts contains it
(426 of the 851,834 eligible review parts)."""
PERSON_TRIGGERS = frozenset(
    {
        "especially",
        "specially",
        "particularly",
        "thanks",
        "thank",
        "called",
        "named",
        "receptionist",
        "receptionists",
        "concierge",
        "waiter",
        "waitress",
        "manager",
        "barman",
        "barmaid",
        "bartender",
        "porter",
        "doorman",
        "housekeeper",
        "cleaner",
        "driver",
        "chef",
        "host",
        "hostess",
        "owner",
        "guide",
        "lady",
        "gentleman",
        "guy",
        "girl",
        "boy",
    }
)
"""Words that often come right before a person's name in these reviews."""
STAFF_WORDS = PERSON_TRIGGERS | {
    "staff",
    "stuff",
    "reception",
    "desk",
    "front",
    "personnel",
    "employee",
    "employees",
    "attendant",
    "housekeeping",
    "maid",
    "maids",
    "managers",
    "waiters",
    "waitresses",
    "concierges",
    "porters",
    "barmen",
    "bartenders",
    "team",
    "service",
    "mention",
}
"""Words that mark a part as talking about people who work at the hotel."""
MIN_NAME_CHARS = 3


class NameGuard:
    """Decide whether a review part probably names someone.

    Args:
        common_words: Lowercase words that are frequent in the corpus.
    """

    def __init__(self, common_words: Iterable[str]) -> None:
        self._common = frozenset(common_words) | STOP_WORDS

    @classmethod
    def from_texts(
        cls, texts: Iterable[str], min_share: float = COMMON_WORD_MIN_SHARE
    ) -> "NameGuard":
        """Build the common-word list from the corpus's review parts.

        O(total tokens). Each word is counted once per part; it is common if
        at least ``min_share`` of the parts (and at least one) contain it.
        """
        counts: Counter[str] = Counter()
        n_parts = 0
        for text in texts:
            counts.update(set(text.lower().split()))
            n_parts += 1
        min_parts = max(1, math.ceil(min_share * n_parts))
        return cls(w for w, n in counts.items() if n >= min_parts)

    def names_someone(self, text: str) -> bool:
        """True if the part probably names a person.

        Either a trigger word is followed by a rare word (stop words in
        between are skipped, so "thank you maria" and "especially the
        receptionist maria" count), or the part is about staff and contains
        a rare capitalised word.
        """
        return self._after_trigger(text) or self._capitalised_near_staff(text)

    def _capitalised_near_staff(self, text: str) -> bool:
        words = text.split()
        if not STAFF_WORDS.intersection(w.lower() for w in words):
            return False
        return any(
            len(w) >= MIN_NAME_CHARS
            and w[0].isupper()
            and not w.isupper()
            and w.lower() not in self._common
            for w in words
        )

    def _after_trigger(self, text: str) -> bool:
        tokens = text.lower().split()
        for i, token in enumerate(tokens):
            if token not in PERSON_TRIGGERS:
                continue
            following = (t for t in tokens[i + 1 :] if t not in STOP_WORDS)
            word = next(following, None)
            if word is None or word in PERSON_TRIGGERS:
                continue
            if word not in self._common and not word.isdigit():
                return True
        return False
