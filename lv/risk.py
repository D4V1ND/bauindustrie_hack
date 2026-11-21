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
from lv.model import LV, LineItem, Prediction, Risk, RiskSummary

# The scale. Starting values, to be tuned with the estimator.
PRICE_RISK = {"copied": 5, "similar": 25, "adjusted": 45, "reference": 75, "none": 100}  # by step 3 method
SPREAD, SPREAD_RISK = 0.10, 40  # "similar", but the past prices behind it lie more than 10% apart
DESCRIPTION_RISK = {"complete": 10, "gaps": 50, "vague": 90}
PRICE_SHARE = 0.6  # of an item's score; the description has the rest
BATCH = 25  # items per description call
PRICED = ("copied", "similar", "adjusted")


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
        price_risk, price_reason = _price_risk(item.prediction)
        value, basis = _value(item)
        item.risk = Risk(
            score=round(PRICE_SHARE * price_risk + (1 - PRICE_SHARE) * DESCRIPTION_RISK[check.description])
            if check else price_risk,
            price_risk=price_risk, price_reason=price_reason,
            description=check.description if check else None,
            description_risk=DESCRIPTION_RISK[check.description] if check else None,
            missing=[text.strip() for text in check.missing][:3] if check else [],
            description_reason=check.why.strip() if check else "",
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
        value=round(sum(risk.value or 0.0 for risk in normal), 2),
        basis={basis: sum(1 for risk in normal if risk.value_basis == basis) for basis in ("price", "reference", "assumed")})


def _price_risk(prediction: Prediction | None) -> tuple[int, str]:
    """How unsure the price is, and one sentence saying why. Reads only what step 3 saved."""
    method = prediction.method if prediction else "none"
    if method == "similar" and prediction.median and (prediction.max - prediction.min) / prediction.median > SPREAD:
        return SPREAD_RISK, (f"The {prediction.count} past prices behind it lie more than {SPREAD:.0%} apart: "
                             f"{prediction.min:.2f} to {prediction.max:.2f}.")
    return PRICE_RISK[method], {
        "copied": "The price is copied from an identical past item.",
        "similar": "The price comes from a past item the judge called the same."
        if prediction and prediction.count == 1 else "The price comes from past items the judge called the same.",
        "adjusted": "The price is calculated between a smaller and a larger size.",
        "reference": "Only related past items were found, none the same. No price.",
        "none": "No comparable past item was found. No price."}[method]


def _value(item: LineItem) -> tuple[float | None, str]:
    """Roughly what the item is worth, for weighting only: the found price, else the middle price of the related
    past items, each times the quantity. `None` with "assumed" when there is neither."""
    prediction, quantity = item.prediction, item.quantity or 0.0
    if quantity and prediction and prediction.method in PRICED and prediction.median is not None:
        return round(prediction.median * quantity, 2), "price"
    if quantity and prediction and prediction.method == "reference" and prediction.sources:
        return round(statistics.median(source.unit_price for source in prediction.sources) * quantity, 2), "reference"
    return None, "assumed"


def _lines(described: Described) -> list[str]:
    item = described.item
    lines = [f"### {item.id} | unit {item.unit} | qty {item.quantity or 0:.10g} | type {item.item_type or '-'}",
             f"{_cut(item.short_text, 140)} — {_cut(item.long_text, 600)}"]
    if described.refers_to:
        lines.append(f"REFERS TO PREVIOUS ITEM: {_cut(described.refers_to, 300)}")
    return lines + [""]
