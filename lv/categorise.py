"""Step 2: sort the items of a new LV into trades and item types, for the overview (SPEC.md, step 2).

Three levels above the line item: trade -> item type -> group. Types are for the overview, not for prices.
The AI proposes and assigns; code checks every answer against the lists and adds up the quantities.
"""
import math
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from lv.model import LV, ItemType

# ponytail: 17 stand-in names from the first test; swap this one constant when an official trade list is chosen
TRADES = (
    "Baustelleneinrichtung", "Planung und Dokumentation", "Rohrleitungsbau", "Armaturen", "Maschinentechnik",
    "Stahl- und Metallbau", "Mess-, Steuer- und Regelungstechnik", "Demontage und Rückbau", "Erdarbeiten",
    "Betonarbeiten", "Straßenbau", "Entwässerungskanalarbeiten", "Wasserhaltung", "Verkehrssicherung",
    "Prüfung und Inbetriebnahme", "Stundenlohnarbeiten", "Sonstiges",
)
OTHER = "Sonstiges"  # the item type, and its trade, for an item that fits no type of the list
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
