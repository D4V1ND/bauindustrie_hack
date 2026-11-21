"""Step 4: a risk score per line item and one for the whole LV (SPEC.md, step 4).

Code scores how unsure the price is, from what step 3 saved. An AI model reads each item's text and says whether
it states what an estimator needs to price it. A score is a place on a scale from 0 to 100, not a probability.
"""
import statistics
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from lv.categorise import _cut
from lv.match import Described, describe, same_item
from lv.model import LV, Comment, LineItem, Risk, RiskSummary

# The scale. Starting values, to be tuned with the estimator.
PRICE_RISK = {"copied": 5, "similar": 25, "adjusted": 45, "reference": 75, "none": 100}  # by step 3 method
SPREAD, SPREAD_RISK = 0.10, 40  # "similar", but the past prices behind it lie more than 10% apart
DESCRIPTION_RISK = {"complete": 10, "gaps": 50, "vague": 90}
PRICE_SHARE = 0.6  # of an item's score; the description has the rest
BATCH = 25  # items per description call
PRICED = ("copied", "similar", "adjusted", "reference")
DOUBT_RISK = 75  # a found price the estimator doubts: worth no more than related items


class Checked(BaseModel):
    id: str  # a line item
    description: Literal["complete", "gaps", "vague"]
    missing: list[str]  # what the text does not state, a few words each
    why: str  # one sentence


class Checks(BaseModel):
    """The answer for one batch of items."""
    items: list[Checked]


def assess(lv: LV, ask: Callable[[list[str]], list[Checks | None]] | None) -> None:
    """Gives every item its risk, and the LV its risk summary. Runs after step 3. Without `ask` the descriptions
    are not checked and an item's score is its price risk alone.

    Identical items are checked once. An item the model gave no answer for keeps its price risk as its score.
    Called again without `ask` (after a comment), it keeps the descriptions checked before: nothing is asked twice.
    """
    everything = describe(lv)
    first: dict[tuple, Described] = {}
    for described in everything:
        first.setdefault(same_item(described), described)
    found: dict[str, Checked] = {}
    if ask and first:
        waiting = list(first.values())
        batches = [waiting[start:start + BATCH] for start in range(0, len(waiting), BATCH)]
        answers = ask(["\n".join(line for described in batch for line in _lines(described)) for batch in batches])
        found = {checked.id: checked for answer in answers if answer for checked in answer.items}

    for described in everything:
        item, check = described.item, found.get(first[same_item(described)].item.id)
        before = None if ask else item.risk  # a recalculation: the description as it was checked
        description = check.description if check else before.description if before else None
        price_risk, price_reason = _price_risk(item)
        value, basis = _value(item)
        entered = _entered(item) is not None  # the estimator's own price: the description no longer counts
        score = price_risk if entered or not description else round(
            PRICE_SHARE * price_risk + (1 - PRICE_SHARE) * DESCRIPTION_RISK[description])
        has_price = entered or bool(item.prediction and item.prediction.method in PRICED
                                    and item.prediction.median is not None)
        item.risk = Risk(
            score=score, confidence=100 - score if has_price else 0,
            price_risk=price_risk, price_reason=price_reason,
            description=description, description_risk=DESCRIPTION_RISK[description] if description else None,
            missing=[text.strip() for text in check.missing][:3] if check else before.missing if before else [],
            description_reason=check.why.strip() if check else before.description_reason if before else "",
            value=value, value_basis=basis)

    # An item nothing was found for counts as a typical item: the middle value of the items that have one.
    values = [item.risk.value for item in lv.items if item.risk.value is not None]
    typical = round(statistics.median(values), 2) if values else None
    for item in lv.items:
        if item.risk.value is None:
            item.risk.value = typical
    normal = [item.risk for item in lv.items if item.kind == "normal"]
    weights = [risk.value or 1.0 for risk in normal]  # no value anywhere in the LV: every item weighs the same
    lv.risk_summary = RiskSummary(
        score=round(sum(w * risk.score for w, risk in zip(weights, normal)) / sum(weights)) if normal else None,
        confidence=round(sum(w * risk.confidence for w, risk in zip(weights, normal)) / sum(weights)) if normal else None,
        value=round(sum(risk.value or 0.0 for risk in normal), 2),
        basis={basis: sum(1 for risk in normal if risk.value_basis == basis) for basis in ("price", "reference", "assumed")})


def comment(lv: LV, item_id: str, text: str = "", price: float | None = None, doubt: bool = False) -> LineItem:
    """Saves the estimator's comment on one item (by id or item number) and recalculates every score and the LV's.
    A price gives the item confidence 100; a doubt lowers a found price. No model is asked."""
    item = next((item for item in lv.items if item.id in (item_id, f"{lv.project}/{item_id}")), None)
    if item is None:
        raise ValueError(f"{lv.project} has no item {item_id}.")
    item.comments.append(Comment(text=text.strip(), price=price, effect="doubt" if doubt else "note"))
    assess(lv, None)
    return item


def _decided(item: LineItem) -> Comment | None:
    """The estimator's last word on the price: the latest comment with a price or a doubt. Notes decide nothing."""
    return next((c for c in reversed(item.comments) if c.price is not None or c.effect == "doubt"), None)


def _entered(item: LineItem) -> float | None:
    """The estimator's own unit price, if that is their last word."""
    decided = _decided(item)
    return decided.price if decided else None


def _price_risk(item: LineItem) -> tuple[int, str]:
    """How unsure the price is, and one sentence saying why. Reads only what step 3 saved and the comments."""
    prediction = item.prediction
    method = prediction.method if prediction else "none"
    if _entered(item) is not None:
        return 0, "The price was entered by the estimator."
    if _decided(item) and method in PRICED:
        return DOUBT_RISK, "The estimator doubts the found price."
    if method == "similar" and prediction.median and (prediction.max - prediction.min) / prediction.median > SPREAD:
        return SPREAD_RISK, (f"The {prediction.count} past prices behind it lie more than {SPREAD:.0%} apart: "
                             f"{prediction.min:.2f} to {prediction.max:.2f}.")
    return PRICE_RISK[method], {
        "copied": "The price is copied from an identical past item.",
        "similar": "The price comes from a past item the judge called the same."
        if prediction and prediction.count == 1 else "The price comes from past items the judge called the same.",
        "adjusted": "The price is calculated between a smaller and a larger size.",
        "reference": "Only related past items were found, none the same: the price is their middle price.",
        "none": "No comparable past item was found. No price."
        + (f" The nearest past items differ; their prices span {prediction.min:.2f} to {prediction.max:.2f}."
           if prediction and prediction.min is not None else "")}[method]


def _value(item: LineItem) -> tuple[float | None, str]:
    """Roughly what the item is worth, for weighting only: the entered or found price times the quantity. `None`
    with "assumed" when there is none (a span of differing items is no value)."""
    prediction, quantity = item.prediction, item.quantity or 0.0
    if quantity and _entered(item) is not None:
        return round(_entered(item) * quantity, 2), "price"
    if quantity and prediction and prediction.method in PRICED and prediction.median is not None:
        return round(prediction.median * quantity, 2), "price"
    return None, "assumed"


def _lines(described: Described) -> list[str]:
    item = described.item
    lines = [f"### {item.id} | unit {item.unit} | qty {item.quantity or 0:.10g} | type {item.item_type or '-'}",
             f"{_cut(item.short_text, 140)} — {_cut(item.long_text, 600)}"]
    if described.refers_to:
        lines.append(f"REFERS TO PREVIOUS ITEM: {_cut(described.refers_to, 300)}")
    return lines + [""]
