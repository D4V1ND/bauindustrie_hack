"""Step 3: look up each item of a new LV in the price library and record what was found (SPEC.md, step 3).

The judge (an AI model) only compares texts. Code decides the method and does all arithmetic. No price is ever
sent to the judge or written into `unit_price`.
"""
import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from lv.match import Described, describe, group_label, searcher
from lv.model import LV, LineItem, MethodSum, Prediction, Source, Summary

METHODS = ("copied", "similar", "adjusted", "reference", "none")


class Judged(BaseModel):
    """The judge's verdict on one candidate. Field names are the ones the judging rules name."""
    cand: str  # id of the candidate
    verdict: Literal["same", "size_only", "type_only", "different"]
    dimension: str | None  # dimension, cand_size and target_size: only for "size_only"
    cand_size: float | None
    target_size: float | None
    difference: str


class Verdicts(BaseModel):
    """The judge's answer for one new item."""
    id: str
    judged: list[Judged]
    reason: str


# Takes one text block per item (see judge_input) and returns one answer per block, or None where it has none.
Judge = Callable[[list[str]], list[Verdicts | None]]


def predict(new: LV, library: list[LV], judge: Judge | None) -> None:
    """Gives every item of the new LV its prediction, and the LV its summary. Without a judge: copy only."""
    # An item without a price, or priced at zero, is no evidence for a price.
    past_items = [past for lv in library for past in describe(lv) if past.item.unit_price]
    by_group: dict[str, list[LineItem]] = defaultdict(list)
    for past in past_items:
        by_group[group_label(past.item)].append(past.item)  # worked out here, so an old library file cannot be stale

    unmatched: list[Described] = []
    for described in describe(new):
        exact = by_group.get(group_label(described.item))
        if exact:
            sources = [Source(id=past.id, unit_price=past.unit_price, verdict="exact") for past in exact]
            described.item.prediction = _priced("copied", sources, "Exact match: same short text, long text and unit.")
        else:
            unmatched.append(described)

    if judge is None:
        for described in unmatched:
            described.item.prediction = Prediction(
                method="none", reason="No exact match in the price library. Search and judge were switched off.")
    else:
        search = searcher(past_items)
        candidates = {described.item.id: search(described) for described in unmatched}
        to_judge = [described for described in unmatched if candidates[described.item.id]]
        answers = judge([judge_input(d, candidates[d.item.id]) for d in to_judge]) if to_judge else []
        answer_for = {d.item.id: answer for d, answer in zip(to_judge, answers, strict=True)}
        for described in unmatched:
            found = candidates[described.item.id]
            described.item.prediction = _from_verdicts(found, answer_for.get(described.item.id)) if found else Prediction(
                method="none", reason="No library item with the same unit and a similar text.")
    new.summary = summarise(new.items)


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
        return Prediction(method="none", reason="The judge gave no answer for this item.")
    price = {candidate.item.id: candidate.item.unit_price for candidate in candidates}  # best candidate first
    first = {}
    for entry in answer.judged:
        if entry.cand in price:  # a verdict for an id that was not offered is ignored
            first.setdefault(entry.cand, entry)
    judged = [first[cand] for cand in price if cand in first]

    def source(entry: Judged) -> Source:
        return Source(id=entry.cand, unit_price=price[entry.cand], verdict=entry.verdict, difference=entry.difference)

    sized = [j for j in judged if j.verdict == "size_only" and j.cand_size is not None and j.target_size is not None]
    same = [j for j in judged if j.verdict == "same"] + [j for j in sized if j.cand_size == j.target_size]
    if same:
        return _priced("similar", [source(j) for j in same], answer.reason)

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
                result=result, reason=answer.reason)

    references = [j for j in judged if j.verdict in ("size_only", "type_only")]
    if references:
        return Prediction(method="reference", sources=[source(j) for j in references], reason=answer.reason)
    return Prediction(method="none", reason=answer.reason)


def _priced(method: str, sources: list[Source], reason: str) -> Prediction:
    prices = [source.unit_price for source in sources]
    return Prediction(method=method, min=min(prices), max=max(prices), median=round(statistics.median(prices), 2),
                      mean=round(statistics.fmean(prices), 2), count=len(prices), sources=sources, reason=reason)


def summarise(items: list[LineItem]) -> Summary:
    """Items per method, and median x quantity added up over the normal items. Methods without a price add nothing."""
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
