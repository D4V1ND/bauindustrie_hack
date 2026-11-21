"""The line item table: the one shape every LV file is read into (SPEC.md, step 0)."""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, computed_field


class Source(BaseModel):
    """One past line item behind a prediction, or shown next to it as a reference."""
    id: str  # a line item in the price library
    unit_price: float
    verdict: Literal["exact", "same", "size_only", "type_only"]  # "exact": same group label, no judge needed
    difference: str = ""


class Prediction(BaseModel):
    """Step 3: what the lookup in the price library found for one line item (SPEC.md, step 3)."""
    method: Literal["copied", "similar", "adjusted", "reference", "none"]
    min: float | None = None
    max: float | None = None
    median: float | None = None  # the value used for totals
    mean: float | None = None
    count: int = 0  # number of past prices behind min, max, median and mean
    sources: list[Source] = []
    rule: str | None = None  # rule, calculation and result: only for "adjusted"
    calculation: str | None = None
    result: float | None = None
    reason: str = ""
    judged_item: str | None = None  # set on a copy: the identical item of this LV that was searched and judged


class MethodSum(BaseModel):
    items: int
    total: float  # sum of median x quantity over the normal items


class Summary(BaseModel):
    methods: dict[str, MethodSum]
    total: float


class ItemType(BaseModel):
    """Step 2: one item type of an LV's own list, with the one trade it belongs to."""
    name: str
    trade: str
    description: str = ""


class LineItem(BaseModel):
    id: str  # project + "/" + oz, unique across all projects
    oz: str
    section: list[str]
    short_text: str
    long_text: str
    quantity: float | None
    unit: str
    unit_price: float | None = None  # empty in a new LV; never filled by the tool
    total_price: float | None = None
    kind: Literal["normal", "optional"] = "normal"
    read_by: Literal["gaeb", "pdf", "ai"] = "gaeb"  # "pdf": texts read by code from a PDF print; "ai": not built
    group: str = ""  # step 1: the same label for every exact match, inside one LV and across the price library
    item_type: str = ""  # step 2: one type from the LV's list, or "Sonstiges"
    trade: str = ""  # step 2: the trade of that item type
    item_type_reason: str = ""  # step 2: one sentence on why the item got this type; text to show, nothing more
    prediction: Prediction | None = None  # step 3; never written into unit_price


class LV(BaseModel):
    project: str
    name: str
    date: date | None
    source_file: str
    items: list[LineItem]
    item_types: list[ItemType] = []  # step 2: the LV's own list of item types
    summary: Summary | None = None  # step 3: items and sums per method, and the project total

    @computed_field
    @property
    def priced(self) -> bool:
        return any(item.unit_price is not None for item in self.items)


def save(lv: LV, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lv.project}.json"
    path.write_text(lv.model_dump_json(indent=1), encoding="utf-8")
    return path


def load(path: Path) -> LV:
    return LV.model_validate_json(path.read_text(encoding="utf-8"))
