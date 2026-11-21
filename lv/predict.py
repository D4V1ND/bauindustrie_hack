"""Step 3: look up each item of a new LV in the price library and record what was found (SPEC.md, step 3).

The judge (an AI model) only compares texts. Code decides the method and does all arithmetic. No price is ever
sent to the judge or written into `unit_price`.
"""
import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from lv.match import Described, Embed, describe, embed_text, group_label, same_item, searcher
from lv.model import LV, LineItem, MethodSum, Prediction, Source, Summary

METHODS = ("copied", "similar", "adjusted", "reference", "none")
NO_ANSWER = "The judge gave no answer for this item."


class Judged(BaseModel):
    """The judge's verdict on one candidate. Field names are the ones the judging rules name."""
    cand: str  # id of the candidate
    verdict: Literal["same", "size_only", "type_only", "different"]
    dimension: str | None  # dimension, cand_size and target_size: only for "size_only"
    cand_size: float | None
    target_size: float | None
    difference: str
    why: str = ""  # one sentence: why this is the same thing, or why it is not


class Verdicts(BaseModel):
    """The judge's answer for one new item."""
    id: str
    judged: list[Judged]
    reason: str


class JudgeAnswer(BaseModel):
    """What one judge call returns: one set of verdicts per item it was asked about."""
    items: list[Verdicts]


# Takes questions (one item each, or a pack of items; see judge_input and _pack_input) and returns one answer per
# question, or None where it has none.
Judge = Callable[[list[str]], list[JudgeAnswer | None]]
MAX_PACK = 15  # items in one judge call
MAX_SHOWN = 3  # candidates kept with a prediction; the judge still reads the whole shortlist
BEST_FIRST = {"same": 0, "size_only": 1, "type_only": 2, "different": 3}


def predict(new: LV, library: list[LV], judge: Judge | None, packed: bool = True, embed: Embed | None = None) -> None:
    """Gives every item of the new LV its prediction, and the LV its summary. Without a judge: copy only.

    `packed=False` asks the judge about every item on its own, as before packs existed (used to compare the two).
    `embed` adds candidates by meaning to the candidates by words.
    """
    # An item without a price, or priced at zero, is no evidence for a price.
    past_items = [past for lv in library for past in describe(lv) if past.item.unit_price]
    by_group: dict[str, list[LineItem]] = defaultdict(list)
    for past in past_items:
        by_group[group_label(past.item)].append(past.item)  # worked out here, so an old library file cannot be stale

    unmatched: list[Described] = []
    for described in describe(new):
        exact = by_group.get(group_label(described.item))
        if exact:
            sources = [Source(id=past.id, short_text=past.short_text, unit_price=past.unit_price, verdict="exact")
                       for past in exact]
            described.item.prediction = _priced("copied", sources, "Exact match: same short text, long text and unit.")
        else:
            unmatched.append(described)

    first: dict[tuple, Described] = {}  # identical items are searched and judged once: the first of each set
    for described in unmatched:
        first.setdefault(same_item(described), described)

    if judge is None:
        for described in first.values():
            described.item.prediction = Prediction(
                method="none", reason="No exact match in the price library. Search and judge were switched off.")
    else:
        search = searcher(past_items, embed)
        if embed:
            embed([embed_text(d) for d in first.values()])  # all new items in one go; each search then finds its vector saved
        candidates = {described.item.id: search(described) for described in first.values()}
        verdicts = _ask_judge([d for d in first.values() if candidates[d.item.id]], candidates, judge, packed)
        for described in first.values():
            found = candidates[described.item.id]
            described.item.prediction = _from_verdicts(found, verdicts.get(described.item.id)) if found else Prediction(
                method="none", reason="No library item with the same unit and a similar text.")

    for described in unmatched:  # copy back, and record which item was actually judged
        judged = first[same_item(described)].item
        if described.item is not judged:
            described.item.prediction = judged.prediction.model_copy(deep=True, update={"judged_item": judged.id})
    new.summary = summarise(new.items)


def _ask_judge(to_judge: list[Described], candidates: dict[str, list[Described]], judge: Judge,
               packed: bool) -> dict[str, Verdicts | None]:
    """The judge's verdicts per item id. Items that step 2 sorted under one item type go out together in a pack;
    an item whose part of a pack answer does not fit is asked again on its own."""
    if not to_judge:
        return {}
    singles: list[Described] = []  # step 2 did not run for these (or packing is off): asked one by one, as before
    by_type: dict[str, list[Described]] = defaultdict(list)
    for described in to_judge:
        (by_type[described.item.item_type] if packed and described.item.item_type else singles).append(described)
    packs = [members[start:start + MAX_PACK] for members in by_type.values()
             for start in range(0, len(members), MAX_PACK)]

    def own(described: Described) -> str:
        return judge_input(described, candidates[described.item.id])

    answers = judge([own(d) for d in singles] + [_pack_input(pack, own) for pack in packs])
    verdicts = {d.item.id: _only(answer, d.item.id) for d, answer in zip(singles, answers)}
    again: list[Described] = []
    for pack, answer in zip(packs, answers[len(singles):], strict=True):
        for described in pack:
            fitting = _fitting(answer, described.item.id, [c.item.id for c in candidates[described.item.id]])
            verdicts[described.item.id] = fitting
            if fitting is None:
                again.append(described)
    if again:
        try:
            answers = judge([own(d) for d in again])
        except RuntimeError:  # every one of these calls failed: the items count as unanswered, the rest stands
            answers = [None for _ in again]
        verdicts |= {d.item.id: _only(answer, d.item.id) for d, answer in zip(again, answers, strict=True)}
    return verdicts


def _pack_input(pack: list[Described], own: Callable[[Described], str]) -> str:
    """One hint line, then every item in exactly the form of a single question."""
    item = pack[0].item
    counted = "1 new item below was" if len(pack) == 1 else f"{len(pack)} new items below were"
    hint = (f'HINT: the {counted} sorted under "{item.item_type}" (trade: {item.trade}) by an earlier step. '
            "Decide by the texts only.")
    return "\n\n".join([hint, *(own(described) for described in pack)])


def _only(answer: JudgeAnswer | None, id: str) -> Verdicts | None:
    """The answer to a question about one item. As before packs: candidate ids are checked later, one by one."""
    if answer is None:
        return None
    named = [verdicts for verdicts in answer.items if verdicts.id == id]
    return named[0] if named else answer.items[0] if len(answer.items) == 1 else None


def _fitting(answer: JudgeAnswer | None, id: str, own_candidates: list[str]) -> Verdicts | None:
    """An item's part of a pack answer, if it is there exactly once and names each of the item's own candidates
    exactly once and no other. In a pack a stray id may be a mix-up with a neighbour, so nothing is guessed."""
    named = [verdicts for verdicts in answer.items if verdicts.id == id] if answer else []
    if len(named) == 1 and sorted(judged.cand for judged in named[0].judged) == sorted(own_candidates):
        return named[0]
    return None


def judge_input(new: Described, candidates: list[Described]) -> str:
    """What the judge reads for one item, in the layout its rules describe. Texts only: never a price."""
    item = new.item
    lines = [f"### {item.id} | unit {item.unit} | qty {_number(item.quantity or 0)}"
             + (" | SURCHARGE ITEM" if new.surcharge else ""),
             f"NEW: {_cut(item.short_text, 140)} — {_cut(item.long_text, 300)}"]
    if new.refers_to:
        lines.append(f"NEW REFERS TO PREVIOUS ITEM: {_cut(new.refers_to, 220)}")
    for candidate in candidates:
        refers = f" [REFERS TO PREVIOUS ITEM: {_cut(candidate.refers_to, 160)}]" if candidate.refers_to else ""
        past = candidate.item
        lines.append(f"- {past.id}: {_cut(past.short_text, 140)} — {_cut(past.long_text, 220)}{refers}")
    return "\n".join(lines)


def _from_verdicts(candidates: list[Described], answer: Verdicts | None) -> Prediction:
    if answer is None:
        return Prediction(method="none", reason=NO_ANSWER)
    price = {candidate.item.id: candidate.item.unit_price for candidate in candidates}  # best candidate first
    text = {candidate.item.id: candidate.item.short_text for candidate in candidates}
    first = {}
    for entry in answer.judged:
        if entry.cand in price:  # a verdict for an id that was not offered is ignored
            first.setdefault(entry.cand, entry)
    judged = sorted((first[cand] for cand in price if cand in first), key=lambda j: BEST_FIRST[j.verdict])

    def source(entry: Judged) -> Source:
        return Source(id=entry.cand, short_text=text[entry.cand], unit_price=price[entry.cand], verdict=entry.verdict,
                      difference=entry.difference, why=entry.why)

    prediction = _decide(judged, price, answer.reason, source)
    used = {s.id for s in prediction.sources}  # what the price rests on comes first, then the nearest misses
    prediction.judged = [source(j) for j in sorted(judged, key=lambda j: j.cand not in used)[:MAX_SHOWN]]
    if prediction.method == "none" and prediction.judged:  # no price, only a span: the nearest past items, which differ
        prices = [s.unit_price for s in prediction.judged]
        prediction.min, prediction.max, prediction.count = min(prices), max(prices), len(prices)
    return prediction


def _decide(judged: list[Judged], price: dict[str, float], reason: str, source: Callable[[Judged], Source]) -> Prediction:
    """The method and the price from the verdicts: same first, then two sizes around the target, then references."""
    sized = [j for j in judged if j.verdict == "size_only" and j.cand_size is not None and j.target_size is not None]
    same = [j for j in judged if j.verdict == "same"] + [j for j in sized if j.cand_size == j.target_size]
    if same:
        return _priced("similar", [source(j) for j in same], reason)

    smaller = [j for j in sized if j.cand_size < j.target_size]
    larger = [j for j in sized if j.cand_size > j.target_size]
    if smaller and larger:
        low, high = max(smaller, key=lambda j: j.cand_size), min(larger, key=lambda j: j.cand_size)
        # Strict on purpose: two sizes that do not clearly belong together give a reference, not a price.
        if low.target_size == high.target_size and _name(low.dimension) == _name(high.dimension):
            low_price, high_price, target = price[low.cand], price[high.cand], low.target_size
            result = round(low_price + (target - low.cand_size) / (high.cand_size - low.cand_size)
                           * (high_price - low_price), 2)
            return Prediction(
                method="adjusted", min=min(low_price, high_price), max=max(low_price, high_price),
                median=result, mean=result, count=2, sources=[source(low), source(high)],
                rule=f"linear interpolation by {low.dimension or 'size'}",
                calculation=(f"{_number(low_price)} + ({_number(target)} - {_number(low.cand_size)}) / "
                             f"({_number(high.cand_size)} - {_number(low.cand_size)}) x "
                             f"({_number(high_price)} - {_number(low_price)})"),
                result=result, reason=reason)

    references = [j for j in judged if j.verdict in ("size_only", "type_only")][:MAX_SHOWN]
    if references:  # the same kind of thing, another size or type: their middle price is the suggestion
        return _priced("reference", [source(j) for j in references], reason)
    return Prediction(method="none", reason=reason)


def _priced(method: str, sources: list[Source], reason: str) -> Prediction:
    prices = [source.unit_price for source in sources]
    return Prediction(method=method, min=min(prices), max=max(prices), median=round(statistics.median(prices), 2),
                      mean=round(statistics.fmean(prices), 2), count=len(prices), sources=sources, reason=reason)


def summarise(items: list[LineItem]) -> Summary:
    """Items per method, and median x quantity added up over the normal items. "none" has no price and adds nothing."""
    methods = {method: MethodSum(items=0, total=0.0) for method in METHODS}
    for item in items:
        entry = methods[item.prediction.method]
        entry.items += 1
        if item.kind == "normal" and item.prediction.median is not None:
            entry.total = round(entry.total + item.prediction.median * (item.quantity or 0), 2)
    return Summary(methods=methods, total=round(sum(entry.total for entry in methods.values()), 2))


def _cut(text: str, length: int) -> str:
    return " ".join(text.split())[:length]


def _number(value: float) -> str:
    return f"{value:.10g}"  # 80.0 -> "80", 7777.77 -> "7777.77"


def _name(dimension: str | None) -> str:
    return (dimension or "").strip().casefold()
