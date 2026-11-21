"""Step 3: look up each item of a new LV in the price library and record what was found (SPEC.md, step 3).

Code decides everything here and does all arithmetic. No price is ever written into `unit_price`.
"""
import statistics
from collections import defaultdict

from lv.match import group_label
from lv.model import LV, LineItem, MethodSum, Prediction, Source, Summary

METHODS = ("copied", "similar", "adjusted", "reference", "none")


def predict(new: LV, library: list[LV], judge: None) -> None:
    """Gives every item of the new LV its prediction, and the LV its summary."""
    by_group: dict[str, list[LineItem]] = defaultdict(list)
    for past in library:
        for item in past.items:
            if item.unit_price:  # an item without a price, or priced at zero, is no evidence for a price
                by_group[group_label(item)].append(item)  # worked out here, so an old library file cannot be stale

    for item in new.items:
        exact = by_group.get(group_label(item))
        if exact:
            sources = [Source(id=past.id, unit_price=past.unit_price, verdict="exact") for past in exact]
            item.prediction = _priced("copied", sources, "Exact match: same short text, long text and unit.")
        else:
            item.prediction = Prediction(method="none", reason="No exact match in the price library.")
    new.summary = summarise(new.items)


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
