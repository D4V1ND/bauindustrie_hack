"""Step 1: the group label that links exact matches. Step 3: the search for candidates."""
import hashlib
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from lv.model import LV, LineItem

# The dataset's anonymiser spells one hidden word in several ways: [PERSON_115], [PERSON_0000115], PERSON_115 and,
# squeezed into the fixed-width GAEB 90 lines, PER000115. Kind and number say which word it is; the rest is spelling.
# ponytail: shorter squeezed spellings (P125, F0101, ###) could be real words or have lost their number, so they
# are left alone and such items do not link across file formats.
_HIDDEN = re.compile(r"\[?\b(?:(person|ort|firma|adresse|telefon|projekt|nummer|email|url|kennzeichen)_|per)0*(\d+)\]?")


def clean(text: str) -> str:
    """Removes the differences without meaning: letter case, spacing, line-break hyphens, anonymiser spelling."""
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # "We- ge" -> "Wege"
    text = " ".join(text.lower().split())
    return _HIDDEN.sub(lambda hidden: f"[{hidden[1] or 'person'}_{hidden[2]}]", text)


def group_label(item: LineItem) -> str:
    """Same short text, long text and unit give the same label."""
    key = "\x1f".join(clean(text) for text in (item.short_text, item.long_text, item.unit))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# --- step 3: candidate search ------------------------------------------------------------------------------------

MAX_CANDIDATES = 5
MIN_SCORE = 0.10  # similarity from 0 (no word in common) to 1 (same words); below this a candidate is noise

_REPEAT = re.compile(r"\bwie (vor|zuvor)\b|\bwie Pos|Leistung wie", re.I)
_SURCHARGE = re.compile(r"\b(Zulage|Zuschlag|Mehrpreis|Aufpreis|Mehrkosten)", re.I)


@dataclass(frozen=True)
class Described:
    """A line item with the two things its own text does not always say."""
    item: LineItem
    surcharge: bool  # an add-on price on top of another item
    refers_to: str  # for a repeat item ("wie vor, jedoch ..."): the text of the item it refers to; else ""


def describe(lv: LV) -> list[Described]:
    """A repeat item refers to the last fully described item before it in the same LV."""
    described: list[Described] = []
    base: Described | None = None
    for item in lv.items:
        own = f"{item.short_text} {item.long_text[:300]}"
        surcharge = bool(_SURCHARGE.search(own))
        if base and _REPEAT.search(own):
            described.append(Described(item, surcharge or base.surcharge,
                                       f"{base.item.short_text} — {base.item.long_text[:300]}"))
        else:
            base = Described(item, surcharge, "")
            described.append(base)
    return described


def searcher(library: list[Described]) -> Callable[[Described], list[Described]]:
    """Prepares the price library once and returns the search: the candidates for one item, best first.

    Similarity is the cosine of the word counts, with rare words weighing more (TF-IDF). Standard library only.
    ponytail: compares every item with every library item; build a word index if the library grows past ~50,000 items.
    """
    counts = [Counter(_words(past)) for past in library]
    in_items = Counter(word for count in counts for word in count)
    rare = {word: math.log(len(library) / n) + 1 for word, n in in_items.items()}
    unknown = math.log(len(library) or 1) + 1

    def vector(count: Counter) -> dict[str, float]:
        weights = {word: n * rare.get(word, unknown) for word, n in count.items()}
        length = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
        return {word: weight / length for word, weight in weights.items()}

    vectors = [vector(count) for count in counts]

    def search(new: Described) -> list[Described]:
        if not new.item.unit:
            return []
        own = vector(Counter(_words(new)))
        scored = [(sum(weight * vectors[n].get(word, 0.0) for word, weight in own.items()), -n)
                  for n, past in enumerate(library)
                  if past.item.unit == new.item.unit and past.surcharge == new.surcharge]
        best = sorted((pair for pair in scored if pair[0] >= MIN_SCORE), reverse=True)[:MAX_CANDIDATES]
        return [library[-n] for _, n in best]  # equal scores: library order

    return search


def _words(described: Described) -> list[str]:
    item = described.item
    return re.findall(r"[a-zäöüß]+|\d+(?:[.,]\d+)?", clean(f"{described.refers_to} {item.short_text} {item.long_text}"))
