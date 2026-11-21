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

MAX_CANDIDATES = 5  # the shortlist the judge reads
MIN_SCORE = 0.10  # similarity from 0 (no word in common) to 1 (same words); below this a candidate is noise
RRF_DEPTH = 20  # with meaning: each search offers its best 20, and the two lists are merged by rank
RRF_K = 60  # the usual constant of reciprocal rank fusion: one first place does not outweigh good places in both
EMBED_LONG_TEXT = 500  # characters of the long text an embedding sees: kind, material, size and scope come first

Embed = Callable[[list[str]], list[list[float]]]  # texts -> one vector of length 1 per text (lv.ai.embed)

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


def same_item(described: Described) -> tuple[str, str, bool]:
    """Two items of one LV with the same value here are word-for-word the same item, so one decision serves both:
    the same group label and, for a repeat item ("wie vor"), the same item it refers to."""
    return group_label(described.item), described.refers_to, described.surcharge


def embed_text(described: Described) -> str:
    """What an embedding sees: the item a repeat item refers to, the short text and the start of the long text.
    Without anonymiser tokens: they hide a different word in each file, so they mean nothing across projects."""
    item = described.item
    text = clean(f"{described.refers_to} {item.short_text} — {item.long_text[:EMBED_LONG_TEXT]}")
    return " ".join(re.sub(r"\[[a-z]+_\d+\]", " ", text).split())


def searcher(library: list[Described], embed: Embed | None = None) -> Callable[[Described], list[Described]]:
    """Prepares the price library once and returns the search: the candidates for one item, best first.

    By words: the cosine of the word counts, with rare words weighing more (TF-IDF). With `embed` also by meaning:
    the cosine of the embeddings, which finds other words for the same thing ("Handrad" for "Handbetätigung"). The two
    rankings are merged by place (`fuse`) and the best MAX_CANDIDATES are the shortlist. The judge decides.
    ponytail: compares every item with every library item; build an index if the library grows past ~50,000 items.
    """
    meanings = embed([embed_text(past) for past in library]) if embed and library else []
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
        fits = [n for n, past in enumerate(library)
                if past.item.unit == new.item.unit and past.surcharge == new.surcharge]
        own = vector(Counter(_words(new)))
        scored = [(sum(weight * vectors[n].get(word, 0.0) for word, weight in own.items()), -n) for n in fits]
        by_words = [-n for _, n in sorted((pair for pair in scored if pair[0] >= MIN_SCORE), reverse=True)]  # ties: library order
        if not meanings:
            return [library[n] for n in by_words[:MAX_CANDIDATES]]
        query = embed([embed_text(new)])[0]
        by_meaning = sorted(fits, key=lambda n: -math.sumprod(query, meanings[n]))
        return [library[n] for n in fuse(by_words[:RRF_DEPTH], by_meaning[:RRF_DEPTH])[:MAX_CANDIDATES]]

    return search


def fuse(*rankings: list[int]) -> list[int]:
    """Reciprocal rank fusion: in each ranking an item earns 1 / (RRF_K + its place); most points first, ties in
    library order. Places, not scores: word scores (0 to ~0.4) and meaning scores (~0.6 to 0.9) are not on one scale."""
    points: Counter = Counter()
    for ranking in rankings:
        for place, n in enumerate(ranking, start=1):
            points[n] += 1 / (RRF_K + place)
    return sorted(points, key=lambda n: (-points[n], n))


def _words(described: Described) -> list[str]:
    item = described.item
    return re.findall(r"[a-zäöüß]+|\d+(?:[.,]\d+)?", clean(f"{described.refers_to} {item.short_text} {item.long_text}"))
