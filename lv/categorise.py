"""Step 2: sort the items of a new LV into trades and item types, for the overview (SPEC.md, step 2).

Three levels above the line item: trade -> item type -> group. Types are for the overview, not for prices.
The AI proposes and assigns; code checks every answer against the lists and adds up the quantities.
"""
import math
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from lv.match import Described, describe
from lv.model import LV, ItemType

# ponytail: 17 stand-in names from the first test; swap this one constant when an official trade list is chosen
TRADES = (
    "Baustelleneinrichtung", "Planung und Dokumentation", "Rohrleitungsbau", "Armaturen", "Maschinentechnik",
    "Stahl- und Metallbau", "Mess-, Steuer- und Regelungstechnik", "Demontage und Rückbau", "Erdarbeiten",
    "Betonarbeiten", "Straßenbau", "Entwässerungskanalarbeiten", "Wasserhaltung", "Verkehrssicherung",
    "Prüfung und Inbetriebnahme", "Stundenlohnarbeiten", "Sonstiges",
)
OTHER = "Sonstiges"  # the item type, and its trade, for an item that fits no type of the list
BATCH = 25  # items per assignment call
MIN_CAP, MAX_CAP = 20, 100  # a small LV may still have 20 types: with 13, P5 lost its hourly-rate items to "Sonstiges"

_SURCHARGE_NAME = re.compile(r"(Zulage|Zuschlag|Mehrpreis|Aufpreis|Mehrkosten)", re.I)


class ProposedType(BaseModel):
    name: str
    trade: Literal[TRADES]  # the model can only answer with a trade from the fixed list
    description: str  # one line saying what belongs here; helps the assignment


class Proposal(BaseModel):
    """The answer to "which item types does this LV have?"."""
    item_types: list[ProposedType]


def type_cap(items: int) -> int:
    """About one type per four items, but never fewer than 20 allowed and never more than 100."""
    return min(MAX_CAP, max(MIN_CAP, math.ceil(items / 4)))


def propose(lv: LV, ask: Callable[[str], Proposal | None]) -> list[ItemType]:
    """One question per LV. The answer is cleaned in code: unique names, no surcharge type, no catch-all, the cap."""
    cap = type_cap(len(lv.items))
    answer = ask(_proposal_input(lv, cap))
    types: dict[str, ItemType] = {}
    for proposed in answer.item_types if answer else []:
        name = proposed.name.strip()
        if name and name.casefold() not in types and name != OTHER and not _SURCHARGE_NAME.match(name):
            types[name.casefold()] = ItemType(name=name, trade=proposed.trade, description=proposed.description.strip())
    if not types:
        raise RuntimeError(f"The model proposed no usable item type list for {lv.project}.")
    return list(types.values())[:cap]  # ponytail: a list over the cap is cut, not asked again


class Assigned(BaseModel):
    id: str  # a line item
    item_type: str  # checked against the list in code


class Assignments(BaseModel):
    """The answer for one batch of items."""
    items: list[Assigned]


def assign(lv: LV, item_types: list[ItemType], ask: Callable[[list[str]], list[Assignments | None]]) -> None:
    """Gives every item one type of the list and that type's trade. An answer outside the list, or a missing one,
    is asked again once; what is left after that becomes "Sonstiges"."""
    trade_of = {item_type.name: item_type.trade for item_type in item_types} | {OTHER: OTHER}
    waiting = describe(lv)
    for _ in range(2):
        batches = [waiting[start:start + BATCH] for start in range(0, len(waiting), BATCH)]
        answers = ask([_assignment_input(batch, item_types) for batch in batches]) if batches else []
        chosen = {assigned.id: assigned.item_type.strip() for answer in answers if answer for assigned in answer.items}
        for described in waiting:
            name = chosen.get(described.item.id)
            if name in trade_of:
                described.item.item_type, described.item.trade = name, trade_of[name]
        waiting = [described for described in waiting if not described.item.item_type]
    for described in waiting:
        described.item.item_type = described.item.trade = OTHER


def _assignment_input(batch: list[Described], item_types: list[ItemType]) -> str:
    lines = ["ITEM TYPES"]
    lines += [f"- {t.name} (trade: {t.trade}): {t.description}" for t in item_types]
    lines += [f"- {OTHER}: if no type of the list fits", "", "ITEMS"]
    for described in batch:
        item = described.item
        lines.append(f"### {item.id} | section {' > '.join(item.section)} | unit {item.unit}")
        lines.append(f"{_cut(item.short_text, 140)} — {_cut(item.long_text, 300)}")
        if described.refers_to:
            lines.append(f"REFERS TO PREVIOUS ITEM: {_cut(described.refers_to, 220)}")
    return "\n".join(lines)


def overview(lv: LV) -> dict[str, dict[str, dict]]:
    """trade -> item type -> number of items and quantities per unit. Normal items only: an optional item may never
    be built. Quantities are added per unit and never across units. Biggest trade and biggest type first."""
    tree: dict[str, dict[str, dict]] = {}
    for item in lv.items:
        if item.item_type and item.kind == "normal":
            entry = tree.setdefault(item.trade, {}).setdefault(item.item_type, {"items": 0, "quantities": {}})
            entry["items"] += 1
            if item.quantity:
                entry["quantities"][item.unit] = entry["quantities"].get(item.unit, 0.0) + item.quantity

    def size(types: dict[str, dict]) -> int:
        return sum(entry["items"] for entry in types.values())

    return {trade: dict(sorted(types.items(), key=lambda pair: (-pair[1]["items"], pair[0])))
            for trade, types in sorted(tree.items(), key=lambda pair: (-size(pair[1]), pair[0]))}


def _proposal_input(lv: LV, cap: int) -> str:
    lines = [f"At most {cap} item types.",
             "Trades (exact spelling): " + ", ".join(f'"{trade}"' for trade in TRADES) + ".",
             "", f"The {len(lv.items)} line items, each as: id | unit | short text — start of the long text"]
    section = None
    for item in lv.items:
        if item.section != section:
            section = item.section
            lines += ["", "## " + " > ".join(section)]
        lines.append(f"- {item.id} | {item.unit} | {_cut(item.short_text, 140)} — {_cut(item.long_text, 200)}")
    return "\n".join(lines)


def _cut(text: str, length: int) -> str:
    return " ".join(text.split())[:length]
